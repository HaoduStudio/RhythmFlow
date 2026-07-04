from __future__ import annotations

import logging
import subprocess

import numpy as np

from .ffmpeg_tools import FfmpegError, get_ffmpeg


logger = logging.getLogger(__name__)


class AudioDecodeError(RuntimeError):
    """Raised when a media file cannot be decoded to mono PCM."""


def decode_mono(path: str, sr: int = 22050) -> np.ndarray:
    logger.info("Decoding mono audio: path=%s sr=%d", path, sr)
    return _run_decode_command(path, sr)


def decode_mono_window(
    path: str,
    sr: int = 22050,
    *,
    start_s: float = 0.0,
    duration_s: float | None = None,
) -> np.ndarray:
    start = max(0.0, float(start_s))
    duration = None if duration_s is None else max(0.0, float(duration_s))
    if duration == 0.0:
        return np.zeros(0, dtype=np.float32)
    logger.info(
        "Decoding mono audio window: path=%s sr=%d start=%.3f duration=%s",
        path,
        sr,
        start,
        "-" if duration is None else f"{duration:.3f}",
    )
    return _run_decode_command(path, sr, start_s=start, duration_s=duration)


def _run_decode_command(
    path: str,
    sr: int,
    *,
    start_s: float = 0.0,
    duration_s: float | None = None,
) -> np.ndarray:
    cmd = [
        get_ffmpeg(),
        "-hide_banner",
        "-v",
        "error",
    ]
    if start_s > 0.0:
        cmd.extend(["-ss", f"{start_s:.6f}"])
    cmd.extend(
        [
            "-i",
            path,
        ]
    )
    if duration_s is not None:
        cmd.extend(["-t", f"{duration_s:.6f}"])
    cmd.extend(
        [
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-f",
            "f32le",
            "-",
        ]
    )
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        logger.error("Audio decode failed for %s\n%s", path, stderr)
        raise AudioDecodeError(f"Could not decode audio from {path}\n{stderr}") from None
    audio = np.frombuffer(completed.stdout, dtype=np.float32).copy()
    if audio.size == 0:
        logger.error("Audio decode returned no samples: %s", path)
        raise AudioDecodeError(f"No decodable audio found in {path}")
    audio = np.nan_to_num(audio, copy=False)
    max_abs = float(np.max(np.abs(audio))) if audio.size else 0.0
    if max_abs > 1.5:
        logger.error("Decoded audio amplitude is unexpectedly high for %s: %.3f", path, max_abs)
        raise FfmpegError(f"Decoded audio amplitude is unexpectedly high: {max_abs:.3f}")
    logger.info("Decoded %d audio sample(s) from %s", audio.size, path)
    return audio
