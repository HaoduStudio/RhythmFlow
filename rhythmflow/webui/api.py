from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import logging
import os
from pathlib import Path, PureWindowsPath
import re
import threading
import uuid
import webbrowser
from typing import Any

from rhythmflow import __version__
from rhythmflow.config import APP_AUTHOR, APP_NAME, REPOSITORY_URL
from rhythmflow.config import SettingsKeys
from rhythmflow.webui import tasks
from rhythmflow.webui.events import Emitter
from rhythmflow.webui.lxns import (
    LxnsError,
    LxnsReferenceAudioService,
    is_probably_mp3,
    is_reference_audio_cache_path,
)
from rhythmflow.webui.media_server import MediaServer
from rhythmflow.webui.settings import load_settings, save_settings
from rhythmflow.webui.state import AppState, JobBuildError
from rhythmflow.webui import updater
from rhythmflow.webui.updater import UpdateError
from rhythmflow.webui.waveform import compute_waveform

logger = logging.getLogger(__name__)

_VIDEO_FILE_TYPES = ("Video files (*.mp4;*.mkv;*.mov;*.avi;*.webm)", "All files (*.*)")
_AUDIO_FILE_TYPES = ("Audio files (*.mp3;*.m4a;*.wav;*.flac;*.aac;*.ogg)", "All files (*.*)")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class _OsuExportSession:
    output_path: Path
    temp_path: Path
    bytes_written: int = 0


class Api:
    def __init__(self, emitter: Emitter, media_server: MediaServer) -> None:
        self.emitter = emitter
        self.media_server = media_server
        self.state = AppState()
        self.lxns = LxnsReferenceAudioService()
        self._window: Any | None = None
        self._thread: threading.Thread | None = None
        self._busy = False
        self._update_thread: threading.Thread | None = None
        self._update_busy = False
        self._osu_exports: dict[str, _OsuExportSession] = {}

    def set_window(self, window: Any) -> None:
        self._window = window
        self.emitter.bind(window)

    def get_settings(self) -> dict[str, Any]:
        settings = load_settings()
        self.state.update_context(_context_from_settings(settings))
        return settings

    def save_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        saved = save_settings(values or {})
        self.state.update_context(_context_from_settings(saved))
        return saved

    def about_info(self) -> dict[str, Any]:
        return {
            "app_name": APP_NAME,
            "version": __version__,
            "author": APP_AUTHOR,
            "repository": REPOSITORY_URL,
        }

    def open_repository(self) -> None:
        webbrowser.open(REPOSITORY_URL)

    def check_for_updates(self) -> dict[str, Any]:
        if self._busy:
            return {"ok": False, "error": "busy_hint"}
        if self._update_busy:
            return {"ok": False, "error": "update_busy"}

        self._update_busy = True
        self._update_thread = threading.Thread(
            target=self._run_update_check,
            name="rhythmflow-update",
            daemon=True,
        )
        self._update_thread.start()
        return {"ok": True}

    def get_media_base(self) -> str:
        return self.media_server.base_url

    def register_media(self, path: str) -> str:
        return self.media_server.register(str(path))

    def pick_videos(self) -> list[str]:
        return self._open_dialog(allow_multiple=True, file_types=_VIDEO_FILE_TYPES)

    def pick_reference(self) -> str | None:
        result = self._open_dialog(allow_multiple=False, file_types=_AUDIO_FILE_TYPES)
        return result[0] if result else None

    def search_reference_songs(self, game: str, query: str = "") -> dict[str, Any]:
        songs = self.lxns.search_songs(game, query)
        updated_at = self.lxns.get_cache_updated_at(game)
        return {
            "songs": [
                {
                    key: song[key]
                    for key in (
                        "id",
                        "title",
                        "artist",
                        "version",
                        "genre",
                        "difficulty_summary",
                        "difficulties",
                        "asset_song_id",
                    )
                }
                for song in songs
            ],
            "updated_at": updated_at,
        }

    def refresh_reference_songs(self, game: str) -> dict[str, Any]:
        try:
            refreshed = self.lxns.refresh_songs(game)
        except LxnsError as exc:
            logger.warning("Could not refresh LXNS song list: %s", exc)
            return {"ok": False, "error": str(exc)}
        return {
            "songs": [
                {
                    key: song[key]
                    for key in (
                        "id",
                        "title",
                        "artist",
                        "version",
                        "genre",
                        "difficulty_summary",
                        "difficulties",
                        "asset_song_id",
                    )
                }
                for song in refreshed["songs"]
            ],
            "updated_at": refreshed["updated_at"],
        }

    def download_reference_audio(
        self,
        game: str,
        asset_song_id: str,
        title: str,
        persist: bool = False,
    ) -> dict[str, Any]:
        settings = load_settings()
        output_dir = self.state.output_dir or str(settings.get(SettingsKeys.OUTPUT_DIR) or "")
        try:
            path = self.lxns.download_audio(
                game,
                asset_song_id,
                title,
                persist=bool(persist),
                output_dir=output_dir,
            )
        except LxnsError as exc:
            logger.warning("Could not download LXNS reference audio: %s", exc)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": path}

    def pick_output_dir(self) -> str | None:
        if self._window is None:
            return None
        _open, folder = _dialog_kinds()
        result = self._window.create_file_dialog(folder)
        if result:
            return str(result[0])
        return None

    def begin_osu_export(self, filename: str) -> dict[str, Any]:
        try:
            output_dir = self._current_output_dir()
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = _unique_output_path(output_dir / _safe_output_filename(filename))
            token = uuid.uuid4().hex
            temp_path = output_path.with_name(f".{output_path.name}.{token}.part")
            temp_path.write_bytes(b"")
        except OSError as exc:
            logger.warning("Could not start osu export: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

        self._osu_exports[token] = _OsuExportSession(output_path=output_path, temp_path=temp_path)
        return {"ok": True, "token": token, "output_path": str(output_path)}

    def append_osu_export_chunk(self, token: str, chunk_base64: str) -> dict[str, Any]:
        session = self._osu_exports.get(str(token))
        if session is None:
            return {"ok": False, "error": "invalid_export_session"}

        try:
            chunk = base64.b64decode(str(chunk_base64), validate=True)
            with session.temp_path.open("ab") as file:
                file.write(chunk)
            session.bytes_written += len(chunk)
        except (binascii.Error, OSError, ValueError) as exc:
            logger.warning("Could not append osu export chunk: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

        return {"ok": True, "bytes": session.bytes_written}

    def finish_osu_export(self, token: str) -> dict[str, Any]:
        session = self._osu_exports.pop(str(token), None)
        if session is None:
            return {"ok": False, "error": "invalid_export_session"}

        try:
            if session.bytes_written <= 0:
                raise OSError("export produced an empty file")
            session.temp_path.replace(session.output_path)
        except OSError as exc:
            _unlink_quietly(session.temp_path)
            logger.warning("Could not finish osu export: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

        logger.info("osu export saved: %s", session.output_path)
        return {
            "ok": True,
            "output_path": str(session.output_path),
            "bytes": session.bytes_written,
        }

    def abort_osu_export(self, token: str) -> dict[str, Any]:
        session = self._osu_exports.pop(str(token), None)
        if session is not None:
            _unlink_quietly(session.temp_path)
        return {"ok": True}

    def _open_dialog(self, *, allow_multiple: bool, file_types: tuple[str, ...]) -> list[str]:
        if self._window is None:
            return []
        open_kind, _folder = _dialog_kinds()
        result = self._window.create_file_dialog(
            open_kind,
            allow_multiple=allow_multiple,
            file_types=file_types,
        )
        return [str(path) for path in result] if result else []

    def _current_output_dir(self) -> Path:
        settings = load_settings()
        output_dir = (
            self.state.output_dir
            or str(settings.get(SettingsKeys.OUTPUT_DIR) or "")
            or str(Path.cwd() / "output")
        )
        return Path(output_dir).expanduser()

    def sync_rows(self, paths: list[str], context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.state.update_context(context)
        return self.state.sync_rows(list(paths or []))

    def set_nudge(self, row_index: int, value: float) -> dict[str, Any] | None:
        return self.state.set_nudge(int(row_index), float(value))

    def get_rows(self) -> list[dict[str, Any]]:
        return self.state.rows_payload()

    def analyze(
        self,
        videos: list[str],
        reference: str,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._busy:
            return {"ok": False, "error": "busy"}
        context = dict(context or {})
        context["reference_path"] = reference
        self.state.update_context(context)
        if not videos:
            return {"ok": False, "error": "warn_add_video"}
        if not reference:
            return {"ok": False, "error": "warn_choose_reference"}
        if is_reference_audio_cache_path(reference) and not is_probably_mp3(reference):
            return {"ok": False, "error": "warn_reference_cache_invalid"}

        self.state.sync_rows(list(videos))
        language = self.state.language

        def on_result(index: int, data: dict[str, Any]) -> None:
            row = self.state.apply_analyze_result(index, data)
            if row is not None:
                self.emitter.emit("analyze_result", {"row": index, "row_state": row})

        def worker() -> None:
            try:
                tasks.run_analysis(list(videos), reference, language, self.emitter, on_result)
            finally:
                self.emitter.emit(
                    "analyze_finished",
                    {
                        "rows": self.state.rows_payload(),
                        "review_rows": self.state.unconfirmed_review_rows(),
                    },
                )
                self._set_busy(False)

        self._start(worker)
        return {"ok": True}

    def get_review_segments(self) -> dict[str, Any]:
        segments = self.state.review_segments()
        for segment in segments:
            segment["video_url"] = self.media_server.register(segment["video_path"])
            segment["reference_url"] = self.media_server.register(segment["reference_path"])
        return {"segments": segments}

    def get_waveform(self, segment: dict[str, Any]) -> dict[str, Any]:
        try:
            return {"ok": True, **compute_waveform(segment)}
        except Exception as exc:  # noqa: BLE001 - surface decode failures to the UI
            logger.warning("Could not compute waveform: %s", exc, exc_info=True)
            return {"ok": False, "error": str(exc)}

    def apply_review(self, deltas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        return self.state.apply_review(deltas)

    def process(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._busy:
            return {"ok": False, "error": "busy"}
        self.state.update_context(context)
        try:
            jobs = self.state.build_jobs()
        except JobBuildError as exc:
            review_rows = self.state.unconfirmed_review_rows()
            return {"ok": False, "error": exc.key, "review_rows": review_rows}

        language = self.state.language

        def worker() -> None:
            try:
                tasks.run_processing(jobs, language, self.emitter)
            finally:
                self.emitter.emit("process_finished", {"rows": self.state.rows_payload()})
                self._set_busy(False)

        self._start(worker)
        return {"ok": True}

    def _start(self, worker: Any) -> None:
        self._set_busy(True)
        self._thread = threading.Thread(target=worker, name="rhythmflow-task", daemon=True)
        self._thread.start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.emitter.emit("busy", busy)

    def _run_update_check(self) -> None:
        restart_pending = False
        try:
            self._emit_update_status(
                "checking",
                current_version=updater.current_release_name(__version__),
            )
            plan = updater.build_update_plan(__version__)
            payload = {
                "current_version": plan.current_name,
                "latest_version": plan.latest_name,
                "release_url": plan.release_url,
            }
            if not plan.update_available:
                self._emit_update_status("up_to_date", **payload)
                return

            asset_name = plan.asset.name if plan.asset is not None else ""
            self._emit_update_status("downloading", **payload, asset_name=asset_name)
            package = updater.download_update_package(
                plan,
                progress_callback=lambda progress: self._emit_update_status(
                    "downloading",
                    **payload,
                    asset_name=asset_name,
                    **progress,
                ),
            )
            self._emit_update_status("installing", **payload, asset_name=package.asset_name)
            updater.schedule_update_install(package)
            restart_pending = True
            self._emit_update_status("restart_pending", **payload, asset_name=package.asset_name)
            self._close_window_soon()
        except UpdateError as exc:
            logger.warning("Update check failed: %s", exc, exc_info=True)
            self._emit_update_status("error", error_key=exc.key, error=exc.message)
        except Exception as exc:  # noqa: BLE001 - surface updater failures to the UI
            logger.exception("Update check failed unexpectedly")
            self._emit_update_status("error", error_key="update_failed", error=str(exc))
        finally:
            if not restart_pending:
                self._update_busy = False

    def _emit_update_status(self, status: str, **payload: Any) -> None:
        self.emitter.emit("update_status", {"status": status, **payload})

    def _close_window_soon(self) -> None:
        def close_window() -> None:
            try:
                if self._window is not None:
                    self._window.destroy()
                else:
                    os._exit(0)
            except Exception:
                logger.warning("Could not close the window for update restart", exc_info=True)

        threading.Timer(1.0, close_window).start()


def _dialog_kinds() -> tuple[Any, Any]:
    import webview

    file_dialog = getattr(webview, "FileDialog", None)
    if file_dialog is not None:
        return file_dialog.OPEN, file_dialog.FOLDER
    return webview.OPEN_DIALOG, webview.FOLDER_DIALOG


def _context_from_settings(settings: dict[str, Any]) -> dict[str, Any]:
    from rhythmflow.config import SettingsKeys

    return {
        "language": settings.get(SettingsKeys.LANGUAGE),
        "output_dir": settings.get(SettingsKeys.OUTPUT_DIR),
        "output_pattern": settings.get(SettingsKeys.OUTPUT_PATTERN),
        "original_volume": settings.get(SettingsKeys.ORIGINAL_VOLUME),
        "reference_volume": settings.get(SettingsKeys.REFERENCE_VOLUME),
        "mode": settings.get(SettingsKeys.CUT_MODE),
    }


def _safe_output_filename(filename: str) -> str:
    raw = PureWindowsPath(str(filename or "")).name
    raw = Path(raw).name
    cleaned = _INVALID_FILENAME_CHARS.sub("_", raw).strip(" .")
    if not cleaned:
        cleaned = "osu_export.mp4"
    if "." not in cleaned:
        cleaned = f"{cleaned}.mp4"
    if len(cleaned) <= 180:
        return cleaned

    suffix = Path(cleaned).suffix[:16]
    stem_limit = max(1, 180 - len(suffix))
    return f"{Path(cleaned).stem[:stem_limit]}{suffix}"


def _unique_output_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}_{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not find available filename for {path.name}")


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        logger.warning("Could not delete temporary export file: %s", path, exc_info=True)
