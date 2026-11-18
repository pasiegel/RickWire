"""Search (BT4G RSS) and the Latest feeds (YTS API, EZTV API + IMDb show lookup).

Only official machine-readable endpoints are used: BT4G's HTML search sits behind a Cloudflare challenge,
but its `&page=rss` feed is public. Nothing here talks to a debrid service or carries an API key -
results are just magnets that go through the normal add-to-queue flow.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode, urlparse

import httpx

from . import __version__

UA = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) RickWire/{__version__}"
YTS_TRACKERS = [
    "udp://open.demonii.com:1337/announce", "udp://tracker.openbittorrent.com:80", "udp://tracker.coppersurfer.tk:6969",
    "udp://glotorrents.pw:6969/announce", "udp://tracker.opentrackr.org:1337/announce", "udp://torrent.gresille.org:80/announce",
    "udp://p4p.arenabg.com:1337", "udp://tracker.leechers-paradise.org:6969",
]
SEARCH_SORTS = {"relevance": None, "seeders": "seeders", "size": "size", "time": "time"}
SEARCH_CATS = {"all", "movie", "audio", "doc", "app", "other"}
YTS_SORTS = {"latest": "date_added", "rating": "rating", "seeds": "seeds", "downloads": "download_count", "year": "year"}
MAX_IMAGE = 3 * 1024 * 1024
EZTV_PAGE = 50


class DiscoverError(Exception):
    pass


class Discover:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA})
        self._cache: dict[str, tuple[float, object]] = {}
        self._img: dict[str, tuple[str, bytes]] = {}
        self._yts_hosts: set[str] = set()

    async def _get(self, url: str, ttl: int, params: dict | None = None):
        key = url + "?" + urlencode(params or {})
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        try:
            r = await self.client.get(url, params=params)
        except httpx.HTTPError as e:
            raise DiscoverError(f"{urlparse(url).hostname} unreachable ({e.__class__.__name__})") from None
        if r.status_code == 403 and "Just a moment" in r.text[:500]:
            raise DiscoverError(f"{urlparse(url).hostname} is showing a Cloudflare challenge - try again later")
        if r.status_code >= 400:
            raise DiscoverError(f"{urlparse(url).hostname} returned HTTP {r.status_code}")
        self._cache[key] = (time.time(), r)
        if len(self._cache) > 200:
            self._cache.pop(next(iter(self._cache)))
        return r

    # ------------------------------------------------------------------ search
    async def search(self, q: str, sort: str = "relevance", category: str = "all") -> list[dict]:
        q = q.strip()
        if not q:
            return []
        params = {"q": q, "page": "rss"}
        if SEARCH_SORTS.get(sort):
            params["orderby"] = SEARCH_SORTS[sort]
        if category in SEARCH_CATS and category != "all":
            params["category"] = category
        base = self.settings["search_url"].rstrip("/")
        r = await self._get(f"{base}/search", 300, params)
        out = []
        for item in _items(r.content):
            magnet = (item.findtext("link") or "").strip()
            if not magnet.startswith("magnet:"):
                continue
            parts = [p.strip() for p in re.split(r"<br\s*/?>", item.findtext("description") or "")]
            size = parts[1] if len(parts) > 1 else ""
            out.append({
                "title": (item.findtext("title") or "").strip(),
                "magnet": magnet,
                "size": size, "size_bytes": _parse_size(size),
                "category": parts[2] if len(parts) > 2 else "",
                "hash": _btih(magnet),
                "date": _date(item.findtext("pubDate")),
                "source": "BT4G",
            })
        return out

    # ------------------------------------------------------------------ YTS
    async def yts(self, sort: str = "latest", page: int = 1, quality: str = "all", query: str = "") -> dict:
        params = {"limit": 24, "page": max(1, page), "sort_by": YTS_SORTS.get(sort, "date_added")}
        if quality in ("720p", "1080p", "2160p", "3D"):
            params["quality"] = quality
        if query.strip():
            params["query_term"] = query.strip()
        base = self.settings["yts_api"].rstrip("/")
        r = await self._get(f"{base}/list_movies.json", 600, params)
        try:
            data = r.json()["data"]
        except (ValueError, KeyError):
            raise DiscoverError("YTS returned something unexpected") from None
        self._learn_host(r.url)
        movies = []
        for m in data.get("movies") or []:
            torrents = []
            for t in m.get("torrents") or []:
                if not t.get("hash"):
                    continue
                label = f"{t.get('quality', '')} {t.get('type', '')}".strip()
                name = f"{m.get('title_long', m.get('title'))} [{label}] [YTS]"
                torrents.append({
                    "label": label, "size": t.get("size"), "seeds": t.get("seeds"), "peers": t.get("peers"),
                    "codec": t.get("video_codec"), "audio": t.get("audio_channels"),
                    "magnet": _magnet(t["hash"], name, YTS_TRACKERS),
                })
            self._learn_host(m.get("medium_cover_image"))
            movies.append({
                "title": m.get("title"), "year": m.get("year"), "rating": m.get("rating"), "runtime": m.get("runtime"),
                "genres": m.get("genres") or [], "summary": (m.get("summary") or "")[:400],
                "cover": m.get("medium_cover_image"), "imdb": m.get("imdb_code"), "url": m.get("url"),
                "added": m.get("date_uploaded_unix"), "torrents": torrents,
            })
        total = int(data.get("movie_count") or 0)
        return {"movies": movies, "page": data.get("page_number", page), "more": page * params["limit"] < total}

    # ------------------------------------------------------------------ EZTV
    async def eztv(self, page: int = 1, imdb: str = "") -> dict:
        """Latest TV releases from EZTV's JSON API, newest first, 50 per page - or every release of one show
        (by IMDb id). EZTV's HTML search is Cloudflare-challenged; its API isn't."""
        params = {"limit": EZTV_PAGE, "page": max(1, page)}
        if imdb:
            params["imdb_id"] = imdb
        base = self.settings["eztv_api"].rstrip("/")
        r = await self._get(f"{base}/get-torrents", 300, params)
        try:
            data = r.json()
        except ValueError:
            raise DiscoverError("EZTV returned something that isn't JSON") from None
        out = []
        for t in data.get("torrents") or []:
            magnet = (t.get("magnet_url") or "").strip()
            if not magnet.startswith("magnet:"):
                continue
            if "&tr=" not in magnet:   # some entries are bare info-hashes
                magnet += "".join(f"&tr={quote(x, safe='')}" for x in YTS_TRACKERS)
            title = (t.get("title") or t.get("filename") or "").replace(" EZTV", "").strip()
            out.append({
                "title": title, "magnet": magnet, "size_bytes": _int(t.get("size_bytes")),
                "seeds": _int(t.get("seeds")), "peers": _int(t.get("peers")),
                "date": _int(t.get("date_released_unix")) or None,
                "season": _int(t.get("season")), "episode": _int(t.get("episode")),
                "imdb": str(t.get("imdb_id") or ""), "source": "EZTV",
            })
        total = _int(data.get("torrents_count"))
        return {"items": out, "page": params["page"], "total": total, "more": params["page"] * EZTV_PAGE < total}

    async def tv_shows(self, q: str) -> list[dict]:
        """Show-name lookup for the EZTV search box, via IMDb's public title-suggestion feed (TV only)."""
        q = re.sub(r"\s+", " ", q).strip().lower()
        if len(q) < 2:
            return []
        r = await self._get(f"https://v3.sg.media-imdb.com/suggestion/x/{quote(q)}.json", 3600, {"includeVideos": 0})
        try:
            items = r.json().get("d") or []
        except ValueError:
            return []
        out = []
        for x in items:
            if x.get("qid") in ("tvSeries", "tvMiniSeries") and str(x.get("id", "")).startswith("tt"):
                out.append({"imdb": x["id"][2:], "title": x.get("l") or "", "years": x.get("yr") or str(x.get("y") or ""),
                            "cast": x.get("s") or ""})
        return out[:8]

    # ------------------------------------------------------------------ poster proxy
    def image_hosts(self) -> set[str]:
        """Only YTS hosts can be proxied - this is not an open proxy. That's the configured API's domain plus the
        domains the API itself answered from / put in its cover links (YTS moves domains: yts.gg -> yts.pw)."""
        out = set()
        for h in {(urlparse(self.settings["yts_api"]).hostname or "").lower()} | self._yts_hosts:
            root = ".".join(h.split(".")[-2:])
            out |= {h, root, f"img.{root}", f"www.{root}"}
        return out

    def _learn_host(self, url) -> None:
        h = (urlparse(str(url or "")).hostname or "").lower()
        if h and len(self._yts_hosts) < 20:
            self._yts_hosts.add(h)

    async def image(self, url: str) -> tuple[str, bytes]:
        if url in self._img:
            return self._img[url]
        allowed = self.image_hosts()
        for _ in range(4):   # follow redirects by hand so every hop is checked against the allow-list
            u = urlparse(url)
            if u.scheme != "https" or (u.hostname or "").lower() not in allowed:
                raise DiscoverError("Image host not allowed")
            try:
                r = await self.client.get(url, follow_redirects=False)
            except httpx.HTTPError:
                raise DiscoverError("Image unreachable") from None
            if r.status_code not in (301, 302, 303, 307, 308):
                break
            url = str(r.url.join(r.headers.get("location", "")))
        else:
            raise DiscoverError("Too many redirects")
        ctype = r.headers.get("content-type", "").split(";")[0]
        if r.status_code != 200 or not ctype.startswith("image/") or ctype == "image/svg+xml" or len(r.content) > MAX_IMAGE:
            raise DiscoverError("Not an image")
        self._img[url] = (ctype, r.content)
        if len(self._img) > 300:
            self._img.pop(next(iter(self._img)))
        return ctype, r.content


# ---------------------------------------------------------------------- helpers
def _items(content: bytes):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise DiscoverError("Feed could not be parsed") from None
    return root.iter("item")


def _magnet(btih: str, name: str, trackers: list[str]) -> str:
    return f"magnet:?xt=urn:btih:{btih}&dn={quote(name)}" + "".join(f"&tr={quote(t, safe='')}" for t in trackers)


def _btih(magnet: str) -> str | None:
    m = re.search(r"btih:([0-9a-fA-F]{40}|[A-Z2-7]{32})", magnet)
    return m.group(1).lower() if m else None


def _date(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return parsedate_to_datetime(re.sub(r",(\S)", r", \1", s.strip())).timestamp()
    except (TypeError, ValueError):
        return None


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_size(s: str) -> int:
    m = re.match(r"([\d.]+)\s*([KMGT]?)i?B", s or "", re.I)
    if not m:
        return 0
    return int(float(m.group(1)) * 1024 ** "BKMGT".index(m.group(2).upper() or "B"))
