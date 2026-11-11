from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from .. import vault, __version__

USER_AGENT = f"RickWire/{__version__}"


class ProviderError(Exception):
    pass


@dataclass
class RemoteItem:
    name: str | None      # relative path inside the torrent, None if unknown
    size: int
    handle: str           # provider-specific thing passed to resolve()


@dataclass
class RemoteStatus:
    state: str            # queued | downloading | ready | error
    progress: float = 0.0
    name: str | None = None
    size: int = 0
    speed: int = 0
    message: str = ""
    items: list[RemoteItem] = field(default_factory=list)


@dataclass
class Resolved:
    url: str
    filename: str
    size: int = 0


class Provider:
    id = "base"
    name = "Base"
    api_hosts: frozenset[str] = frozenset()   # the only hosts this provider's key may ever be sent to
    supports_hosters = True
    # UI help: where to get the key and what to click
    key_url = ""
    signup_url = ""
    dashboard_url = ""
    key_steps: tuple[str, ...] = ()

    def __init__(self):
        # Hardened client for calls that carry the API key:
        #  * TLS certificate verification on (httpx default, certifi bundle)
        #  * no redirects - a redirect can never carry the key somewhere else
        #  * trust_env=False - HTTP(S)_PROXY / .netrc env vars can't reroute or add credentials
        #  * every request is checked: https only, and only to this provider's API host
        self.client = httpx.AsyncClient(timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=False,
                                        trust_env=False, event_hooks={"request": [self._guard]})

    async def _guard(self, request: httpx.Request):
        if request.url.scheme != "https" or request.url.host not in self.api_hosts:
            raise ProviderError(f"{self.name}: refused to send API key to {request.url.scheme}://{request.url.host}")

    @property
    def key(self) -> str:
        k = vault.get(self.id)
        if not k:
            raise ProviderError(f"No API key configured for {self.name}")
        return k

    def configured(self) -> bool:
        return bool(vault.get(self.id))

    def _check(self, r: httpx.Response) -> dict:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code >= 300:  # redirects are never followed with a key attached - treat as failure
            msg = data.get("error") or data.get("detail") or data.get("message") or r.reason_phrase
            raise ProviderError(f"{self.name}: {vault.scrub(str(msg))} (HTTP {r.status_code})")
        return data

    # --- interface -----------------------------------------------------
    async def account(self) -> dict: ...
    async def add_magnet(self, magnet: str) -> str: ...
    async def add_torrent(self, data: bytes, filename: str) -> str: ...
    async def status(self, remote_id: str) -> RemoteStatus: ...
    async def resolve(self, handle: str) -> Resolved: ...
    async def delete(self, remote_id: str) -> None: ...
    async def unrestrict(self, link: str) -> list[Resolved]: ...

    # --- cache check ---------------------------------------------------
    # "lookup": the provider answers "is this cached?" directly, cheap enough to run automatically.
    # "probe": no such endpoint any more (Real-Debrid and AllDebrid removed theirs) - the torrent is added,
    # checked for being ready instantly, then deleted again. Only run when the user asks for it.
    cache_check = "probe"

    async def cached(self, magnets: dict[str, str]) -> dict[str, bool | None]:
        """{btih: magnet} -> {btih: True (cached) | False | None (couldn't tell)}"""
        return {h: None for h in magnets}
