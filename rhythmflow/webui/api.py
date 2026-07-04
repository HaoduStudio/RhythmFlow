from __future__ import annotations

import logging
import threading
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
from rhythmflow.webui.waveform import compute_waveform

logger = logging.getLogger(__name__)

_VIDEO_FILE_TYPES = ("Video files (*.mp4;*.mkv;*.mov;*.avi;*.webm)", "All files (*.*)")
_AUDIO_FILE_TYPES = ("Audio files (*.mp3;*.m4a;*.wav;*.flac;*.aac;*.ogg)", "All files (*.*)")


class Api:
    def __init__(self, emitter: Emitter, media_server: MediaServer) -> None:
        self.emitter = emitter
        self.media_server = media_server
        self.state = AppState()
        self.lxns = LxnsReferenceAudioService()
        self._window: Any | None = None
        self._thread: threading.Thread | None = None
        self._busy = False

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

    def get_media_base(self) -> str:
        return self.media_server.base_url

    def register_media(self, path: str) -> str:
        return self.media_server.register(str(path))

    def pick_videos(self) -> list[str]:
        return self._open_dialog(allow_multiple=True, file_types=_VIDEO_FILE_TYPES)

    def pick_reference(self) -> str | None:
        result = self._open_dialog(allow_multiple=False, file_types=_AUDIO_FILE_TYPES)
        return result[0] if result else None

    def search_reference_songs(self, game: str, query: str = "") -> list[dict[str, Any]]:
        songs = self.lxns.search_songs(game, query)
        return [
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
        ]

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
