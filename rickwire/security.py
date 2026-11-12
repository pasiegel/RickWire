"""Network lockdown, sessions and phone pairing.

Layers:
  * Only loopback + private (RFC1918 / link-local / ULA) client IPs are served, plus any extra allowed_cidrs.
  * Host header must be an IP literal, localhost or this machine's name (defeats DNS rebinding).
  * Every /api call needs a session cookie: the desktop window gets an admin session via a one-time
    bootstrap token; phones get a device session by pairing with a short-lived 6-digit code.
  * State-changing requests must carry the X-RickWire header (blocks cross-site form/CSRF posts).
  * Admin-only (desktop) routes: API keys, settings, devices. Phones can add/manage downloads only.
  * Remote access (opt-in) only through a trusted reverse proxy such as Nginx Proxy Manager, only over HTTPS,
    and only with the web login (password + optional 2FA) or a phone paired through the proxy. Pairing through
    the proxy needs the long one-time key in the desktop's QR code (the 6-digit code alone only works on the LAN),
    so it still needs someone looking at this PC's screen. Everything that arrives through the proxy counts as
    remote - even if it claims a home address - so a tunnel or misconfigured proxy can't pass as the LAN.
    A request carrying proxy headers from anything that isn't the trusted proxy is refused.
  * With remote access on and a public address set, that address is the phone address: LAN page loads are
    redirected to it, so a phone uses one address (and one sign-in) at home and away.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
import sys
import time
import uuid
from dataclasses import dataclass

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse

from .config import LOCAL_TOKEN_FILE, Settings
from .webauth import WebLogin

COOKIE = "rw_session"
PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10")]
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _ip(addr: str):
    ip = ipaddress.ip_address(addr.split("%")[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def client_ip(request: Request) -> str:
    """The real visitor address (behind the trusted proxy: what the proxy reported)."""
    return getattr(request.state, "client_ip", None) or (request.client.host if request.client else "")


def is_remote(request: Request) -> bool:
    return bool(getattr(request.state, "remote", False))


def is_loopback(request: Request) -> bool:
    if is_remote(request) or not request.client:
        return False
    try:
        return _ip(client_ip(request)).is_loopback
    except ValueError:
        return False


_ips_cache: tuple[float, list[str]] = (0.0, [])


def local_ips() -> list[str]:
    """This PC's LAN addresses. Cached for a minute: resolving the computer's own name can be slow (macOS asks mDNS
    and may wait 5+ seconds), and this is called on every desktop /api/me."""
    global _ips_cache
    if time.time() - _ips_cache[0] < 60:
        return list(_ips_cache[1])
    ips = _local_ips()
    _ips_cache = (time.time(), ips)
    return list(ips)


def _local_ips() -> list[str]:
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # no packets sent
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        if sys.platform == "darwin":   # the UDP trick above already found the address; skip the slow name lookup
            raise OSError
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127."))


def public_url(settings: Settings) -> str | None:
    """The address phones should use when remote access through the proxy is on."""
    hosts = settings["public_hosts"] or []
    if settings["remote_enabled"] and settings["lan_enabled"] and settings["trusted_proxies"] and hosts:
        return f"https://{hosts[0]}"
    return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Session:
    admin: bool
    device_id: str | None = None
    name: str = "Desktop"
    login: bool = False          # signed in with the web username/password


class Auth:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.bootstrap = secrets.token_urlsafe(32)          # one-time, handed to the desktop window
        self.desktop_tokens: set[str] = set()
        self.local_token = secrets.token_urlsafe(32)        # magnet-handler -> running instance
        LOCAL_TOKEN_FILE.write_text(self.local_token, "utf-8")
        self.pair_code: str | None = None
        self.pair_key: str | None = None                    # long one-time key carried only in the QR code
        self.pair_expires = 0.0
        self.pair_fails = 0
        self.web = WebLogin(settings)

    # ----- desktop
    def redeem_bootstrap(self, token: str) -> str | None:
        if self.bootstrap and hmac.compare_digest(token, self.bootstrap):
            self.bootstrap = secrets.token_urlsafe(32)      # rotate: single use
            session = secrets.token_urlsafe(32)
            self.desktop_tokens.add(session)
            return session
        return None

    def new_bootstrap_url(self, port: int) -> str:
        return f"http://127.0.0.1:{port}/auth/bootstrap?t={self.bootstrap}"

    # ----- pairing
    def start_pairing(self) -> tuple[str, float]:
        self.pair_code = f"{secrets.randbelow(10**6):06d}"
        self.pair_key = secrets.token_urlsafe(24)
        self.pair_expires = time.time() + 300
        self.pair_fails = 0
        return self.pair_code, self.pair_expires

    def complete_pairing(self, code: str, name: str, remote: bool = False, key: str = "") -> str:
        if not self.pair_code or time.time() > self.pair_expires:
            raise HTTPException(403, "No pairing in progress - start one from the desktop app")
        ok = hmac.compare_digest(code.strip(), self.pair_code)
        if remote:   # from outside the 6-digit code is guessable - the QR's long key is required too
            ok = ok and bool(key) and hmac.compare_digest(key.strip(), self.pair_key or "")
        if not ok:
            self.pair_fails += 1
            if self.pair_fails >= 5:
                self.pair_code = self.pair_key = None
            if remote and not key:
                raise HTTPException(403, "Scan the QR code on your PC to pair - the typed code alone only works on the home address")
            raise HTTPException(403, "Wrong code, Morty")
        self.pair_code = self.pair_key = None
        token = secrets.token_urlsafe(32)
        devices = list(self.settings["devices"])
        devices.append({"id": uuid.uuid4().hex[:12], "name": (name or "Phone")[:40], "hash": _hash(token),
                        "created": time.time(), "last_seen": time.time(), "remote": remote})
        self.settings.update({"devices": devices})
        return token

    def revoke(self, device_id: str):
        self.settings.update({"devices": [d for d in self.settings["devices"] if d["id"] != device_id]})

    # ----- lookup
    def session_for(self, request: Request) -> Session | None:
        token = request.cookies.get(COOKIE)
        if not token:
            return None
        web = self.web.session(token, client_ip(request))
        if web:
            return Session(admin=False, device_id=f"login:{web['id']}", name=self.web.data.get("user", "web"), login=True)
        remote = is_remote(request)
        if not remote and token in self.desktop_tokens and is_loopback(request):
            return Session(admin=True)
        h = _hash(token)
        for d in self.settings["devices"]:
            if remote and not d.get("remote"):
                continue         # from outside: never a desktop token or a phone paired on the LAN
            if hmac.compare_digest(d["hash"], h):
                if time.time() - d.get("last_seen", 0) > 3600:
                    d["last_seen"] = time.time()
                    self.settings.save()
                return Session(admin=False, device_id=d["id"], name=d["name"])
        return None

    def check_local(self, request: Request) -> bool:
        tok = request.headers.get("x-rickwire-local", "")
        return is_loopback(request) and hmac.compare_digest(tok, self.local_token)


def require(request: Request, admin: bool = False) -> Session:
    sess: Session | None = request.state.session
    if not sess:
        raise HTTPException(401, "Not paired")
    if admin and not sess.admin:
        raise HTTPException(403, "Desktop only")
    return sess


class Guard(BaseHTTPMiddleware):
    def __init__(self, app, auth: Auth, settings: Settings):
        super().__init__(app)
        self.auth = auth
        self.settings = settings
        self.hostname = socket.gethostname().lower()

    @staticmethod
    def _in(addr: str, cidrs) -> bool:
        try:
            ip = _ip(addr)
        except ValueError:
            return False
        nets = []
        for c in cidrs:
            try:
                nets.append(ipaddress.ip_network(c, strict=False))
            except ValueError:
                pass
        return any(ip in n for n in nets if n.version == ip.version)

    def _ip_allowed(self, addr: str) -> bool:
        return self._in(addr, [str(n) for n in PRIVATE_NETS] + list(self.settings["allowed_cidrs"] or []))

    def _via_proxy(self, peer: str) -> bool:
        return bool(self.settings["remote_enabled"]) and self._in(peer, self.settings["trusted_proxies"] or [])

    def _host_allowed(self, host: str) -> bool:
        host = host.lower()
        if host.startswith("["):
            h = host[1:host.find("]")]
        else:
            h = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if h in ("localhost", self.hostname, f"{self.hostname}.local", f"{self.hostname}.lan"):
            return True
        if h in {x.strip().lower() for x in self.settings["public_hosts"] or []}:
            return True
        try:
            ipaddress.ip_address(h)
            return True
        except ValueError:
            return False

    async def dispatch(self, request: Request, call_next):
        if not request.client:
            return PlainTextResponse("Nope. Wrong dimension.", 403)
        peer = request.client.host
        # the desktop listener (127.0.0.1:local_port) is never "the proxy", even when NPM runs on this PC
        desktop_port = request.scope.get("server", (None, None))[1] == self.settings["local_port"]
        if not desktop_port and self._via_proxy(peer):
            # NPM appends the address it saw to X-Forwarded-For; the last entry is the one it vouches for
            real = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
            proto = request.headers.get("x-forwarded-proto", "").split(",")[-1].strip().lower()
            if proto != "https":
                return PlainTextResponse("Remote access only works over HTTPS - turn on SSL / Force SSL in your proxy.", 403)
            try:
                _ip(real)
            except ValueError:
                real = "unknown"
            request.state.client_ip, request.state.remote = real, True
        else:
            if not self._ip_allowed(peer):
                return PlainTextResponse("Nope. Wrong dimension.", 403)
            if any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip")):
                return PlainTextResponse("This request came through a proxy RickWire doesn't trust. To use one, turn on "
                                         "Remote access in RickWire's Settings and add the proxy's address.", 403)
            request.state.client_ip, request.state.remote = peer, False
            # one phone address: with remote access on, LAN page loads go to the public address
            pub = public_url(self.settings)
            if (pub and not desktop_port and request.method == "GET" and "text/html" in request.headers.get("accept", "")
                    and not request.url.path.startswith(("/api/", "/auth/")) and not _ip(peer).is_loopback):
                q = f"?{request.url.query}" if request.url.query else ""
                return RedirectResponse(pub + request.url.path + q, 307)
        if not self._host_allowed(request.headers.get("host", "")):
            return PlainTextResponse("Bad host", 421)
        path = request.url.path
        if path.startswith("/api/") and request.method not in SAFE_METHODS:
            if request.headers.get("x-rickwire") != "1" and not path.startswith("/api/local/"):
                return JSONResponse({"detail": "Missing X-RickWire header"}, 403)
            origin = request.headers.get("origin")
            if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin request refused"}, 403)
        request.state.session = self.auth.session_for(request)
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": response.headers.get("Cache-Control", "no-store"),
            "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response
