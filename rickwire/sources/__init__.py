"""Link classification: decide which pipeline handles a pasted link."""
from __future__ import annotations

import re
from urllib.parse import urlparse

from . import gofile, mega, ytdl

# Hosters that debrid services unlock. If no debrid is configured these fall back to direct HTTP.
DEBRID_HOSTERS = {
    "mega.nz", "mega.co.nz", "rapidgator.net", "rg.to", "1fichier.com", "nitroflare.com", "turbobit.net",
    "ddownload.com", "katfile.com", "filefactory.com", "mediafire.com", "uptobox.com", "uploaded.net",
    "hitfile.net", "fikper.com", "clicknupload.click", "dropapk.to", "filespace.com", "usersdrive.com",
    "hexload.com", "drop.download", "uploady.io", "send.cm", "megaup.net", "4shared.com", "upstore.net",
    "alfafile.net", "wupfile.com", "filestore.me", "isra.cloud", "worldbytez.com", "pixeldrain.com",
}

MODES = ("auto", "debrid", "ytdlp", "direct", "native")


def host_of(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def is_magnet(s: str) -> bool:
    return s.lower().startswith("magnet:?")


def classify(url: str, have_hoster_debrid: bool, hoster_mode: str) -> str:
    """-> torrent | torrent_url | mega | gofile | debrid | ytdlp | direct"""
    if is_magnet(url):
        return "torrent"
    if urlparse(url).path.lower().endswith(".torrent"):
        return "torrent_url"
    host = host_of(url)
    hoster = any(host == h or host.endswith("." + h) for h in DEBRID_HOSTERS)
    if mega.is_mega(url):
        return "debrid" if have_hoster_debrid and hoster_mode == "debrid_first" else "mega"
    if gofile.is_gofile(url):
        return "debrid" if have_hoster_debrid and not gofile.has_token() else "gofile"
    if hoster and have_hoster_debrid:
        return "debrid"
    if ytdl.has_extractor(url):
        return "ytdlp"
    return "direct"


_LINK = re.compile(r"(magnet:\?[^\s\"'<>]+|https?://[^\s\"'<>]+)", re.I)


def extract_links(text: str) -> list[str]:
    seen, out = set(), []
    for m in _LINK.findall(text or ""):
        m = m.rstrip(").,;]")
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out
