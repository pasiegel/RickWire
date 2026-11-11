"""Optional rename & sort for finished downloads: release names -> Plex/Jellyfin-style folders.

    Movies\\Title (Year)\\Title (Year).mkv
    TV\\Show\\Season 02\\Show - S02E07.mkv

Only video files (and subtitles that belong to them) are moved. Anything that doesn't look like a movie or an
episode is left where it is. Nothing is ever overwritten, and nothing outside the download folder is touched.
"""
from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .sources.http_dl import safe_name

VIDEO = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".flv"}
SUBS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".sup"}

_EP = re.compile(r"\bS(\d{1,2})[ ._-]?E(\d{1,3})(?:[ ._-]?-?E(\d{1,3}))?\b", re.I)
_EP_X = re.compile(r"\b(\d{1,2})x(\d{2,3})\b")
_SEASON = re.compile(r"\b(?:S(\d{1,2})(?!\d)|Season[ ._-]?(\d{1,2}))\b", re.I)
_YEAR = re.compile(r"(?<![\dA-Za-z])(19\d{2}|20\d{2})(?![\dA-Za-z])")
_JUNK = re.compile(
    r"\b(2160p|1080[pi]|720p|576p|480p|4k|uhd|hdr\d*|dv|dolby|web[ ._-]?dl|webrip|web|bluray|blu[ ._-]?ray|bdrip|brrip|"
    r"remux|hdtv|dvdrip|dvd|x26[45]|h[ ._]?26[45]|hevc|avc|aac\d?|ac3|dts|ddp?\d|atmos|10bit|proper|repack|"
    r"extended|unrated|imax|multi|complete|internal|amzn|nf|dsnp|hmax|atvp|yts|yify|rarbg|eztv)\b", re.I)


@dataclass
class Guess:
    kind: str                 # "movie" | "tv"
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode2: int | None = None


def _clean(s: str) -> str:
    s = re.sub(r"^\s*[\[(][^\])]*[\])]\s*", "", s)           # leading [group] tag
    s = re.sub(r"[._]+", " ", s)
    s = re.sub(r"[\[(]\s*[\])]", "", s)
    s = re.sub(r"\s+", " ", s).strip(" -([")
    if s and s == s.lower():
        s = s.title()
    return s


def guess(name: str) -> Guess | None:
    """Best guess at what a release / file name is. None = not sure, leave it alone."""
    stem = Path(name).stem if Path(name).suffix.lower() in VIDEO | SUBS else name
    text = re.sub(r"[._]", " ", stem)
    m = _EP.search(text) or _EP_X.search(text)
    if m:
        title, year = _title_year(text[:m.start()])
        if not title:
            return None
        ep2 = int(m.group(3)) if m.re is _EP and m.group(3) else None
        return Guess("tv", title, year, int(m.group(1)), int(m.group(2)), ep2)
    m = _SEASON.search(text)
    if m and m.start() > 0:
        title, year = _title_year(text[:m.start()])
        if title:
            return Guess("tv", title, year, int(m.group(1) or m.group(2)))
    years = [y for y in _YEAR.finditer(text) if y.start() > 0]
    if years:
        y = years[-1]
        title = _clean(text[:y.start()])
        if title:
            return Guess("movie", title, int(y.group(1)))
    j = _JUNK.search(text)
    if j and j.start() > 0 and "[" not in text[:j.start()]:   # "Game [FitGirl Repack]" isn't a movie
        title = _clean(text[:j.start()])
        if title:
            return Guess("movie", title)
    return None


def _title_year(s: str) -> tuple[str, int | None]:
    years = [y for y in _YEAR.finditer(s) if y.start() > 0]
    if years:
        return _clean(s[:years[-1].start()]), int(years[-1].group(1))
    return _clean(s), None


def target_for(g: Guess, ext: str, fallback_name: str) -> Path:
    if g.kind == "movie":
        base = safe_name(f"{g.title} ({g.year})" if g.year else g.title)
        return Path("Movies") / base / f"{base}{ext}"
    show = safe_name(f"{g.title} ({g.year})" if g.year else g.title)
    season = Path("TV") / show / f"Season {g.season:02d}"
    if g.episode is None:
        return season / safe_name(fallback_name)
    ep = f"S{g.season:02d}E{g.episode:02d}" + (f"-E{g.episode2:02d}" if g.episode2 else "")
    return season / f"{show} - {ep}{ext}"


def _is_sample(p: Path) -> bool:
    return bool(re.search(r"(^|[ ._\-\[(])sample([ ._\-\])]|$)", p.stem, re.I))


def plan(job_name: str, files: list[Path], root: Path) -> list[tuple[Path, Path]]:
    """-> [(from, to)] absolute paths. Empty if nothing could be recognised."""
    root = root.resolve()
    videos = [f for f in files if f.suffix.lower() in VIDEO and f.is_file() and not _is_sample(f)]
    if not videos:
        return []
    job_guess = guess(job_name)
    moves: list[tuple[Path, Path]] = []
    if job_guess and job_guess.kind == "movie" or (not job_guess and len(videos) == 1):
        # a movie: the biggest video is the film, the rest (extras) stay put
        main = max(videos, key=lambda f: f.stat().st_size)
        g = job_guess if job_guess and job_guess.kind == "movie" else guess(main.name)
        if g and g.kind == "tv" and g.episode is not None:
            pass   # the file itself says it's an episode - fall through to the TV handling below
        elif g:
            moves.append((main, root / target_for(g, main.suffix.lower(), main.name)))
            videos = []
    for v in videos:
        g = guess(v.name)
        if g and g.kind == "tv" and job_guess and job_guess.kind == "tv":
            g.title, g.year = job_guess.title, job_guess.year or g.year    # pack name is usually cleaner
        elif not g and job_guess and job_guess.kind == "tv" and job_guess.episode is not None and len(videos) == 1:
            g = job_guess
        if g and g.kind == "tv" and g.season is not None:
            moves.append((v, root / target_for(g, v.suffix.lower(), v.name)))
    # subtitles follow their video
    subs = [f for f in files if f.suffix.lower() in SUBS and f.is_file()]
    video_moves = list(moves)
    for v_from, v_to in video_moves:
        for s in subs:
            if len(video_moves) == 1 or s.stem.lower().startswith(v_from.stem.lower()):
                lang = s.stem[len(v_from.stem):].strip(" ._-") if s.stem.lower().startswith(v_from.stem.lower()) else s.stem
                lang = re.sub(r"^\d+[ _-]*", "", lang)        # "2_English" -> "English"
                suffix = f".{safe_name(lang)}" if lang else ""
                moves.append((s, v_to.with_name(v_to.stem + suffix + s.suffix.lower())))
    return [(a, b) for a, b in moves if root in b.resolve().parents and a.resolve() != b.resolve()]


def _free(p: Path, taken: set[Path]) -> Path:
    if not p.exists() and p not in taken:
        return p
    for i in range(2, 100):
        c = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not c.exists() and c not in taken:
            return c
    raise OSError(f"Too many copies of {p.name}")


def apply(moves: list[tuple[Path, Path]], root: Path, cleanup: Path | None = None) -> dict[Path, Path]:
    """Move files. Never overwrites: an existing target gets " (2)". Returns {old: new}."""
    done: dict[Path, Path] = {}
    taken: set[Path] = set()
    for src, dst in moves:
        dst = _free(dst, taken)
        taken.add(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        done[src] = dst
    if cleanup:   # tidy the empty folders the release came in (only inside the download folder)
        root = root.resolve()
        for d in sorted({p.parent for p in done}, key=lambda x: len(x.parts), reverse=True):
            while root in d.resolve().parents and d.exists() and not any(d.iterdir()):
                d.rmdir()
                d = d.parent
    return done


def common_folder(paths: list[Path]) -> Path | None:
    if not paths:
        return None
    try:
        return Path(os.path.commonpath([str(p.parent) for p in paths]))
    except ValueError:
        return None
