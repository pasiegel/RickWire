from .alldebrid import AllDebrid
from .base import Provider, ProviderError, RemoteItem, RemoteStatus, Resolved
from .premiumize import Premiumize
from .realdebrid import RealDebrid
from .torbox import TorBox

ALL: dict[str, Provider] = {p.id: p for p in (RealDebrid(), AllDebrid(), TorBox(), Premiumize())}

__all__ = ["ALL", "Provider", "ProviderError", "RemoteItem", "RemoteStatus", "Resolved"]
