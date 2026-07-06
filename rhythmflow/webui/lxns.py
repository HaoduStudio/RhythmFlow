from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from rhythmflow import __version__
from rhythmflow.config import APP_NAME
from rhythmflow.webui import settings as settings_module

logger = logging.getLogger(__name__)

JsonFetcher = Callable[[str], Any]
Downloader = Callable[[str, Path], None]

_MIN_AUDIO_BYTES = 4096
_SONG_CACHE_TTL_SECONDS = 24 * 60 * 60
_SONG_CACHE_DIR_NAME = "lxns_song_cache"


class LxnsError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameConfig:
    api_url: str
    asset_base: str


GAMES: dict[str, GameConfig] = {
    "maimai": GameConfig(
        api_url="https://maimai.lxns.net/api/v0/maimai/song/list?notes=true",
        asset_base="https://assets2.lxns.net/maimai",
    ),
    "chunithm": GameConfig(
        api_url="https://maimai.lxns.net/api/v0/chunithm/song/list?notes=true",
        asset_base="https://assets2.lxns.net/chunithm",
    ),
}

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SPACE_RUN = re.compile(r"\s+")

_LEVEL_LABELS: dict[str, dict[int, str]] = {
    "maimai": {
        0: "BASIC",
        1: "ADVANCED",
        2: "EXPERT",
        3: "MASTER",
        4: "Re:MASTER",
    },
    "chunithm": {
        0: "BASIC",
        1: "ADVANCED",
        2: "EXPERT",
        3: "MASTER",
        4: "ULTIMA",
        5: "WORLD'S END",
    },
}


class LxnsReferenceAudioService:
    def __init__(
        self,
        *,
        fetch_json: JsonFetcher | None = None,
        download_file: Downloader | None = None,
    ) -> None:
        self._fetch_json = fetch_json or _fetch_json
        self._download_file = download_file or _download_file
        self._song_cache: dict[str, tuple[list[dict[str, Any]], str]] = {}

    def search_songs(self, game: str, query: str = "") -> list[dict[str, Any]]:
        normalized_game = _normalize_game(game)
        songs, _ = self._load_songs(normalized_game)
        needle = _normalize_query(query)
        if not needle:
            return songs
        return [song for song in songs if needle in song["search_text"]][:100]

    def get_cache_updated_at(self, game: str) -> str | None:
        normalized_game = _normalize_game(game)
        cached = self._song_cache.get(normalized_game)
        if cached is not None:
            return cached[1]
        return _read_disk_cache_updated_at(normalized_game)

    def refresh_songs(self, game: str) -> dict[str, Any]:
        normalized_game = _normalize_game(game)
        songs, updated_at = self._load_songs(normalized_game, force_refresh=True)
        return {"songs": songs, "updated_at": updated_at}

    def download_audio(
        self,
        game: str,
        asset_song_id: str | int,
        title: str,
        *,
        persist: bool = False,
        output_dir: str | None = None,
    ) -> str:
        normalized_game = _normalize_game(game)
        song_id = str(asset_song_id).strip()
        if not song_id:
            raise LxnsError("Missing song id")

        if persist:
            if not output_dir:
                raise LxnsError("Output directory is required")
            target_dir = Path(output_dir).expanduser()
        else:
            target_dir = settings_module.config_dir() / "reference_audio_cache"

        target_dir.mkdir(parents=True, exist_ok=True)
        filename = safe_filename(f"{normalized_game}_{song_id}_{title}") + ".mp3"
        target = target_dir / filename
        if is_probably_mp3(target):
            return str(target)
        if target.exists():
            target.unlink(missing_ok=True)

        quoted = urllib.parse.quote(song_id, safe="")
        url = f"{GAMES[normalized_game].asset_base}/music/{quoted}.mp3"
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            self._download_file(url, tmp)
            if not is_probably_mp3(tmp):
                raise LxnsError("Downloaded file is not a valid MP3 audio file")
            tmp.replace(target)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
        return str(target)

    def _load_songs(
        self,
        game: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        if not force_refresh:
            cached = self._song_cache.get(game)
            if cached is not None:
                return cached
            disk = _read_disk_cache(game)
            if disk is not None:
                self._song_cache[game] = disk
                return disk
        payload = self._fetch_json(GAMES[game].api_url)
        raw_songs = _extract_songs(payload)
        songs = [_normalize_song(game, raw) for raw in raw_songs]
        updated_at = _now_iso()
        _write_disk_cache(game, songs, updated_at)
        self._song_cache[game] = (songs, updated_at)
        return songs, updated_at


def safe_filename(value: str, fallback: str = "reference_audio") -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("_", value).strip(" ._")
    cleaned = _SPACE_RUN.sub(" ", cleaned)
    if not cleaned:
        cleaned = fallback
    return cleaned[:120]


def is_probably_mp3(path: str | Path) -> bool:
    source = Path(path)
    if not source.is_file() or source.stat().st_size < _MIN_AUDIO_BYTES:
        return False
    try:
        header = source.read_bytes()[:64]
    except OSError:
        return False
    if header.startswith(b"ID3"):
        return True
    if header[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}:
        return True
    stripped = header.lstrip().lower()
    if stripped.startswith((b"<script", b"<!doctype", b"<html", b"<?xml", b"{")):
        return False
    return False


def is_reference_audio_cache_path(path: str | Path) -> bool:
    try:
        source = Path(path).expanduser().resolve()
        cache_dir = (settings_module.config_dir() / "reference_audio_cache").resolve()
        return source.suffix.lower() == ".mp3" and source.parent == cache_dir
    except OSError:
        return False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_cache_fresh(updated_at: str) -> bool:
    try:
        timestamp = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return False
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - timestamp).total_seconds()
    return age < _SONG_CACHE_TTL_SECONDS


def _song_cache_path(game: str) -> Path:
    return settings_module.config_dir() / _SONG_CACHE_DIR_NAME / f"{game}.json"


def _read_disk_cache(game: str) -> tuple[list[dict[str, Any]], str] | None:
    path = _song_cache_path(game)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not read LXNS song cache at %s", path, exc_info=True)
        return None
    if not isinstance(data, dict):
        return None
    updated_at = str(data.get("updated_at") or "")
    raw_songs = data.get("songs")
    if not updated_at or not isinstance(raw_songs, list):
        return None
    if not _is_cache_fresh(updated_at):
        return None
    songs = [song for song in raw_songs if isinstance(song, dict)]
    return songs, updated_at


def _read_disk_cache_updated_at(game: str) -> str | None:
    path = _song_cache_path(game)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    updated_at = str(data.get("updated_at") or "")
    return updated_at or None


def _write_disk_cache(game: str, songs: list[dict[str, Any]], updated_at: str) -> None:
    path = _song_cache_path(game)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"updated_at": updated_at, "songs": songs}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        logger.warning("Could not write LXNS song cache to %s", path, exc_info=True)


def _normalize_game(game: str) -> str:
    key = str(game or "").strip().lower()
    if key not in GAMES:
        raise LxnsError(f"Unsupported game: {game}")
    return key


def _extract_songs(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        songs = payload.get("songs")
    else:
        songs = payload
    if not isinstance(songs, list):
        raise LxnsError("Invalid song list response")
    return [song for song in songs if isinstance(song, dict)]


def _normalize_song(game: str, song: dict[str, Any]) -> dict[str, Any]:
    song_id = str(song.get("id") or "").strip()
    title = str(song.get("title") or song_id or "Untitled").strip()
    artist = str(song.get("artist") or "").strip()
    version = str(song.get("version") or "").strip()
    genre = str(song.get("genre") or "").strip()
    difficulties = _flatten_difficulties(song.get("difficulties"))
    asset_song_id = _asset_song_id(game, song_id, difficulties)
    difficulty_entries = _difficulty_entries(game, difficulties)
    difficulty_summary = _difficulty_summary(difficulty_entries)
    search_text = _normalize_query(
        " ".join([song_id, title, artist, version, genre, difficulty_summary, asset_song_id])
    )
    return {
        "id": song_id,
        "title": title,
        "artist": artist,
        "version": version,
        "genre": genre,
        "difficulty_summary": difficulty_summary,
        "difficulties": difficulty_entries,
        "asset_song_id": asset_song_id,
        "search_text": search_text,
    }


def _flatten_difficulties(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    result: list[dict[str, Any]] = []
    for key in ("standard", "dx", "utage"):
        entries = value.get(key)
        if isinstance(entries, list):
            result.extend(item for item in entries if isinstance(item, dict))
    return result


def _asset_song_id(game: str, song_id: str, difficulties: list[Any]) -> str:
    if game != "chunithm":
        return song_id
    for difficulty in difficulties:
        if not isinstance(difficulty, dict):
            continue
        if not _is_worlds_end(difficulty):
            continue
        origin_id = str(difficulty.get("origin_id") or "").strip()
        if origin_id:
            return origin_id
    return song_id


def _is_worlds_end(difficulty: dict[str, Any]) -> bool:
    index = _difficulty_index(difficulty)
    if index == 5:
        return True
    values = [
        difficulty.get("type"),
        difficulty.get("name"),
        difficulty.get("difficulty"),
        difficulty.get("label"),
    ]
    text = " ".join(str(value or "") for value in values).upper()
    return "WORLD" in text or "WE" == text.strip()


def _difficulty_entries(game: str, difficulties: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int | None]] = set()
    for difficulty in difficulties:
        index = _difficulty_index(difficulty)
        label = _difficulty_label(game, difficulty, index)
        level = str(difficulty.get("level") or difficulty.get("level_value") or "").strip()
        key = (label, level, index)
        if not label or key in seen:
            continue
        seen.add(key)
        result.append({"label": label, "level": level, "index": index})
    return result


def _difficulty_summary(difficulties: list[dict[str, Any]]) -> str:
    parts = [
        " ".join(str(value).strip() for value in (item.get("label"), item.get("level")) if str(value).strip())
        for item in difficulties
    ]
    return " / ".join(part for part in parts if part)


def _difficulty_index(difficulty: dict[str, Any]) -> int | None:
    for key in ("difficulty", "level_index"):
        value = difficulty.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _difficulty_label(game: str, difficulty: dict[str, Any], index: int | None) -> str:
    if index is not None:
        label = _LEVEL_LABELS.get(game, {}).get(index)
        if label:
            return label
    label = difficulty.get("label") or difficulty.get("name") or difficulty.get("type") or ""
    return str(label).strip()


def _normalize_query(query: str) -> str:
    return _SPACE_RUN.sub(" ", str(query or "").casefold()).strip()


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": f"{APP_NAME}/{__version__}",
            "Accept": "application/json,*/*",
        },
    )


def _fetch_json(url: str) -> Any:
    with urllib.request.urlopen(_request(url), timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _download_file(url: str, target: Path) -> None:
    headers = _asset_headers()
    content_type, data = _download_bytes(url, headers)
    if _is_tencent_edge_challenge(content_type, data):
        cookie = _tencent_edge_cookie(data)
        if cookie:
            retry_headers = {**headers, "Cookie": cookie}
            content_type, data = _download_bytes(url, retry_headers)
    if not data:
        raise LxnsError("Downloaded audio is empty")
    if "text/html" in content_type or "javascript" in content_type:
        raise LxnsError("LXNS asset server returned a non-audio response")
    target.write_bytes(data)


def _asset_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
        ),
        "Accept": "audio/mpeg,audio/*;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://maimai.lxns.net/",
    }


def _download_bytes(url: str, headers: dict[str, str]) -> tuple[str, bytes]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            content_type = str(response.headers.get("Content-Type") or "").lower()
            return content_type, response.read()
    except urllib.error.URLError as exc:
        raise LxnsError(f"Could not download reference audio: {exc}") from exc


def _is_tencent_edge_challenge(content_type: str, data: bytes) -> bool:
    if "text/html" not in content_type and not data.lstrip().startswith(b"<script"):
        return False
    text = data.decode("utf-8", "ignore")
    return "__tst_status" in text and "EO_Bot_Ssid" in text


def _tencent_edge_cookie(data: bytes) -> str | None:
    text = data.decode("utf-8", "ignore")
    status = _tencent_edge_status(text)
    session = _tencent_edge_session(text)
    if status is None or session is None:
        return None
    return f"__tst_status={status}#; EO_Bot_Ssid={session}"


def _tencent_edge_status(text: str) -> int | None:
    match = re.search(r"var\s+e=\{(?P<body>.*?)\},t=0", text)
    if not match:
        return None
    values = [int(item) for item in re.findall(r":\s*(\d+)(?=,|}|$)", match.group("body"))]
    return sum(values) if values else None


def _tencent_edge_session(text: str) -> int | None:
    match = re.search(r"EO_Bot_Ssid=.*?[,+]\s*(\d{6,})", text)
    if not match:
        match = re.search(r"EO_Bot_Ssid=.*?(\d{6,})", text)
    return int(match.group(1)) if match else None
