"""API-key storage in the OS credential store (Windows Credential Manager / DPAPI).

Keys are write-only from the HTTP API: they are never returned to any client,
only a masked hint. A logging filter scrubs them from every log record.
"""
from __future__ import annotations

import logging
import sys

import keyring
from keyring.errors import PasswordDeleteError

log = logging.getLogger("rickwire.vault")


class VaultUnavailable(Exception):
    pass


def _pick_backend() -> str | None:
    """Pin an encrypted OS keyring explicitly. A frozen build can't rely on keyring's entry-point discovery,
    and we never want a silent fallback to a plaintext/null backend - no secure store means no saved keys."""
    if sys.platform == "win32":
        from keyring.backends.Windows import WinVaultKeyring
        keyring.set_keyring(WinVaultKeyring())
        return "Windows Credential Manager"
    candidates = []
    try:
        from keyring.backends.SecretService import Keyring as SecretServiceKeyring   # GNOME Keyring, KeePassXC...
        candidates.append((SecretServiceKeyring, "the Secret Service keyring (GNOME Keyring / KWallet)"))
    except Exception:
        pass
    try:
        from keyring.backends.kwallet import DBusKeyring as KWallet
        candidates.append((KWallet, "KWallet"))
    except Exception:
        pass
    for cls, label in candidates:
        try:
            if cls.priority > 0:   # raises / is 0 when the service isn't running
                keyring.set_keyring(cls())
                return label
        except Exception:
            continue
    return None


BACKEND = _pick_backend()
SERVICE = "RickWire"
_known: set[str] = set()
NO_KEYRING = ("No secure keyring is available, so RickWire won't store API keys. Install and unlock "
              "GNOME Keyring (gnome-keyring + libsecret) or KWallet, then restart RickWire.")


def get(name: str) -> str | None:
    if not BACKEND:
        return None
    try:
        value = keyring.get_password(SERVICE, name)
    except Exception as e:   # locked / dismissed keyring prompt
        log.warning("keyring read failed: %s", e.__class__.__name__)
        return None
    if value:
        _known.add(value)
    return value


def put(name: str, value: str) -> None:
    if not BACKEND:
        raise VaultUnavailable(NO_KEYRING)
    value = value.strip()
    keyring.set_password(SERVICE, name, value)
    _known.add(value)


def delete(name: str) -> None:
    if not BACKEND:
        return
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        pass


def mask(value: str | None) -> str | None:
    if not value:
        return None
    return "•" * 8 + value[-4:] if len(value) > 8 else "•" * 8


def scrub(text: str) -> str:
    for secret in _known:
        if secret and secret in text:
            text = text.replace(secret, "***")
    return text


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _known:
            record.msg = scrub(record.getMessage())
            record.args = ()
        return True
