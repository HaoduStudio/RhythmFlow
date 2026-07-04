from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import imageio_ffmpeg


logger = logging.getLogger(__name__)


class FfmpegError(RuntimeError):
    pass

@dataclass(frozen=True)
class MediaProbe:
    path: str
    duration: float
    has_audio: bool
    has_video: bool
    stderr: str = ""


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
_OUT_TIME_MS_RE = re.compile(r"out_time_ms=(\d+)")


def get_ffmpeg() -> str:
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        logger.debug("Using system ffmpeg: %s", system_ffmpeg)
        return system_ffmpeg
    bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    logger.debug("Using imageio-ffmpeg binary: %s", bundled_ffmpeg)
    return bundled_ffmpeg


def parse_timestamp(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_seconds(value: float) -> str:
    return f"{max(0.0, value):.6f}"


def _parse_duration(stderr: str) -> float:
    match = _DURATION_RE.search(stderr)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def probe(path: str) -> MediaProbe:
    logger.info("Probing media: %s", path)
    cmd = [get_ffmpeg(), "-hide_banner", "-i", path]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    stderr = completed.stderr or ""
    has_audio = bool(re.search(r"Stream #\d+:\d+.*Audio:", stderr))
    has_video = bool(re.search(r"Stream #\d+:\d+.*Video:", stderr))
    duration = _parse_duration(stderr)
    if duration <= 0 and "No such file or directory" in stderr:
        logger.error("Media probe failed because file is missing: %s", path)
        raise FfmpegError(f"Cannot read media file: {path}")
    logger.info(
        "Probe result for %s: duration=%.3f has_audio=%s has_video=%s",
        path,
        duration,
        has_audio,
        has_video,
    )
    return MediaProbe(
        path=path,
        duration=duration,
        has_audio=has_audio,
        has_video=has_video,
        stderr=stderr,
    )


def _progress_seconds(line: str) -> float | None:
    ms_match = _OUT_TIME_MS_RE.search(line)
    if ms_match:
        return int(ms_match.group(1)) / 1_000_000
    time_match = _TIME_RE.search(line)
    if time_match:
        return parse_timestamp(":".join(time_match.groups()))
    return None


def run_ffmpeg(
    args: Sequence[str],
    *,
    duration_s: float | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> str:
    cmd = [
        get_ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-progress",
        "pipe:2",
        "-nostats",
        *args,
    ]
    logger.info("Starting ffmpeg command: %s", _redact_command(cmd))
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    stderr_lines: list[str] = []
    last_progress = -1
    assert process.stderr is not None
    for line in process.stderr:
        stderr_lines.append(line.rstrip())
        seconds = _progress_seconds(line)
        if seconds is None or not duration_s or duration_s <= 0:
            continue
        progress = max(0, min(100, int(seconds / duration_s * 100)))
        if progress != last_progress:
            last_progress = progress
            if on_progress:
                on_progress(progress)

    return_code = process.wait()
    stderr = "\n".join(stderr_lines)
    if return_code != 0:
        tail = "\n".join(stderr_lines[-40:])
        logger.error("ffmpeg failed with exit code %s\n%s", return_code, tail)
        raise FfmpegError(f"ffmpeg failed with exit code {return_code}\n{tail}")
    if on_progress:
        on_progress(100)
    logger.info("ffmpeg completed successfully")
    return stderr


def _redact_command(cmd: Sequence[str]) -> str:
    return " ".join(str(part) for part in cmd)
