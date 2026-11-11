"""Optional username/password (+ 2FA) login for the web UI - the way in from outside the home network.

  * The password is stored only as a scrypt hash (config.json, "web_login"); it is never returned by the API.
  * The 2FA secret (TOTP, RFC 6238 - Google Authenticator, Aegis, 1Password...) lives in the OS keyring.
  * Brute force: 5 failures from one address lock that address for 15 minutes; 30 failures in total within
    15 minutes lock every login for 15 minutes. Each failure also costs the caller a short delay.
  * A login gives the same access as a paired phone: downloads, search, Latest. Never keys or settings.
  * Sessions: random 256-bit token in an HttpOnly cookie, stored only as a SHA-256 hash, 30 days.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from urllib.parse import quote

from . import vault

TOTP_KEY = "web_totp"
SESSION_DAYS = 30
MIN_PASSWORD = 10
IP_LIMIT, GLOBAL_LIMIT, WINDOW = 5, 30, 15 * 60


# ---------------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = 2 ** 15, 8, 1
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${dk.hex()}"


def check_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=64 * 1024 * 1024, dklen=len(dk) // 2)
        return hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password(secrets.token_hex(8))   # so a wrong username costs the same time as a wrong password


# ---------------------------------------------------------------------- TOTP
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def totp_match(secret: str, code: str, now: float | None = None) -> int | None:
    """-> the matching time-step counter (allowing one step of clock drift either way), or None."""
    code = "".join(c for c in code if c.isdigit())
    if len(code) != 6:
        return None
    t = int((now or time.time()) // 30)
    for c in (t, t - 1, t + 1):
        if hmac.compare_digest(totp_at(secret, c), code):
            return c
    return None


def otpauth_uri(secret: str, user: str, host: str) -> str:
    label = quote(f"RickWire ({host}):{user}")
    return f"otpauth://totp/{label}?secret={secret}&issuer=RickWire&algorithm=SHA1&digits=6&period=30"


# ---------------------------------------------------------------------- login manager
class LoginError(Exception):
    def __init__(self, msg: str, need_code: bool = False):
        super().__init__(msg)
        self.need_code = need_code


class WebLogin:
    def __init__(self, settings):
        self.settings = settings
        self.fails: dict[str, list[float]] = {}
        self.all_fails: list[float] = []
        self.global_lock_until = 0.0
        self.pending_totp: str | None = None     # set up but not confirmed yet
        self.last_counter = -1                   # a 2FA code can't be used twice

    # --- state
    @property
    def data(self) -> dict:
        return dict(self.settings.get("web_login") or {})

    def _save(self, data: dict):
        self.settings.update({"web_login": data})

    def enabled(self) -> bool:
        d = self.data
        return bool(d.get("user") and d.get("hash"))

    def totp_secret(self) -> str | None:
        return vault.get(TOTP_KEY) if self.data.get("totp") else None

    def status(self) -> dict:
        d = self.data
        return {"enabled": self.enabled(), "user": d.get("user") if self.enabled() else None,
                "totp": bool(d.get("totp")), "sessions": len(d.get("sessions", []))}

    # --- account
    def set_account(self, user: str, password: str):
        user = user.strip()
        if not (1 <= len(user) <= 64) or any(c.isspace() for c in user):
            raise ValueError("Username: 1-64 characters, no spaces")
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")
        d = self.data
        d.update(user=user, hash=hash_password(password), sessions=[], changed=time.time())   # signs everyone out
        self._save(d)

    def remove_account(self):
        vault.delete(TOTP_KEY)
        self.settings.update({"web_login": {}, "remote_enabled": False})

    # --- 2FA
    def totp_begin(self) -> str:
        self.pending_totp = new_totp_secret()
        return self.pending_totp

    def totp_confirm(self, code: str):
        if not self.pending_totp:
            raise ValueError("Start the 2FA setup first")
        if totp_match(self.pending_totp, code) is None:
            raise ValueError("That code doesn't match - check the phone's clock and try the next code")
        vault.put(TOTP_KEY, self.pending_totp)   # raises VaultUnavailable without a keyring
        self.pending_totp = None
        d = self.data
        d.update(totp=True, sessions=[])
        self._save(d)

    def totp_disable(self):
        vault.delete(TOTP_KEY)
        d = self.data
        d["totp"] = False
        self._save(d)

    # --- brute-force limits
    def _prune(self, now: float):
        for ip in list(self.fails):
            self.fails[ip] = [t for t in self.fails[ip] if now - t < WINDOW]
            if not self.fails[ip]:
                del self.fails[ip]
        self.all_fails = [t for t in self.all_fails if now - t < WINDOW]

    def locked(self, ip: str) -> str | None:
        now = time.time()
        self._prune(now)
        if now < self.global_lock_until:
            return "Too many failed sign-ins - logins are paused for a few minutes"
        if len(self.fails.get(ip, [])) >= IP_LIMIT:
            return "Too many failed sign-ins from this address - try again in 15 minutes"
        return None

    def _fail(self, ip: str) -> bool:
        """Record a failure. True when this one triggered the global lock."""
        now = time.time()
        self.fails.setdefault(ip, []).append(now)
        self.all_fails.append(now)
        if len(self.all_fails) >= GLOBAL_LIMIT and now >= self.global_lock_until:
            self.global_lock_until = now + WINDOW
            return True
        return False

    # --- login / sessions
    def login(self, user: str, password: str, code: str, ip: str, agent: str) -> tuple[str, bool]:
        """-> (session token, global_lock_tripped). Raises LoginError."""
        msg = self.locked(ip)
        if msg:
            raise LoginError(msg)
        d = self.data
        ok_user = self.enabled() and hmac.compare_digest(user.strip().encode(), str(d.get("user", "")).encode())
        ok_pw = check_password(password, d["hash"] if ok_user else _DUMMY) and ok_user
        if not ok_pw:
            tripped = self._fail(ip)
            raise LoginError("Wrong username or password" + (" - logins paused" if tripped else ""))
        if d.get("totp"):
            secret = vault.get(TOTP_KEY)
            if not secret:
                raise LoginError("2FA is on but its secret is missing from the keyring - turn 2FA off and on again on the PC")
            if not code:
                raise LoginError("Enter the 6-digit code from your authenticator app", need_code=True)
            counter = totp_match(secret, code)
            if counter is None or counter <= self.last_counter:
                self._fail(ip)
                raise LoginError("Wrong or already-used 2FA code", need_code=True)
            self.last_counter = counter
        self.fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        sessions = [s for s in d.get("sessions", []) if time.time() - s["created"] < SESSION_DAYS * 86400]
        sessions.append({"id": uuid.uuid4().hex[:12], "hash": _hash(token), "created": time.time(),
                         "last_seen": time.time(), "ip": ip, "agent": (agent or "")[:120]})
        d["sessions"] = sessions[-20:]
        self._save(d)
        return token, False

    def session(self, token: str, ip: str) -> dict | None:
        if not token or not self.enabled():
            return None
        h = _hash(token)
        d = self.data
        for s in d.get("sessions", []):
            if hmac.compare_digest(s["hash"], h):
                if time.time() - s["created"] > SESSION_DAYS * 86400:
                    return None
                if time.time() - s.get("last_seen", 0) > 600 or s.get("ip") != ip:
                    s["last_seen"], s["ip"] = time.time(), ip
                    self._save(d)
                return s
        return None

    def logout(self, token: str):
        h = _hash(token or "")
        d = self.data
        d["sessions"] = [s for s in d.get("sessions", []) if not hmac.compare_digest(s["hash"], h)]
        self._save(d)

    def sessions(self) -> list[dict]:
        return [{k: s.get(k) for k in ("id", "created", "last_seen", "ip", "agent")} for s in self.data.get("sessions", [])]

    def revoke(self, sid: str | None = None):
        d = self.data
        d["sessions"] = [s for s in d.get("sessions", []) if sid and s["id"] != sid]
        self._save(d)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
