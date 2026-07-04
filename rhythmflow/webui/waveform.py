from __future__ import annotations

import logging
from typing import Any

import numpy as np

from rhythmflow.config import SR
from rhythmflow.core.audio_io import decode_mono_window

logger = logging.getLogger(__name__)

BASE_WAVEFORM_ADJUST_LIMIT_S = 2.0
WAVEFORM_ADJUST_PADDING_S = 2.0
MAX_WAVEFORM_ADJUST_LIMIT_S = 120.0
MIN_WAVEFORM_DURATION_S = 0.1
ENVELOPE_POINTS = 900


def compute_waveform(segment: dict[str, Any]) -> dict[str, Any]:
    lower, upper = adjustment_bounds(segment)
    ref_start, ref_end, video_start, video_end = _waveform_window_bounds(segment, lower, upper)
    ref_duration = max(MIN_WAVEFORM_DURATION_S, ref_end - ref_start)
    video_duration = max(MIN_WAVEFORM_DURATION_S, video_end - video_start)

    reference_samples = decode_mono_window(
        str(segment["reference_path"]), SR, start_s=ref_start, duration_s=ref_duration
    )
    video_samples = decode_mono_window(
        str(segment["video_path"]), SR, start_s=video_start, duration_s=video_duration
    )

    return {
        "duration_s": _segment_duration(segment),
        "bounds": {"lower": lower, "upper": upper},
        "reference": {
            "envelope": _peak_envelope(reference_samples, ENVELOPE_POINTS).tolist(),
            "window_start_s": ref_start,
            "window_duration_s": reference_samples.size / float(SR),
        },
        "video": {
            "envelope": _peak_envelope(video_samples, ENVELOPE_POINTS).tolist(),
            "window_start_s": video_start,
            "window_duration_s": video_samples.size / float(SR),
        },
    }


def adjustment_bounds(segment: dict[str, Any]) -> tuple[float, float]:
    limit = _adjustment_limit_for_segment(segment)
    lower = max(-limit, -max(0.0, float(segment.get("video_start_s", 0.0))))
    return lower, limit


def _segment_offset_s(segment: dict[str, Any]) -> float:
    return float(segment.get("video_start_s", 0.0)) - float(segment.get("reference_start_s", 0.0))


def _segment_duration(segment: dict[str, Any]) -> float:
    candidates = [
        float(segment.get("reference_end_s", 0.0)) - float(segment.get("reference_start_s", 0.0)),
        float(segment.get("video_end_s", 0.0)) - float(segment.get("video_start_s", 0.0)),
    ]
    positives = [item for item in candidates if item > 0.0]
    if not positives:
        return MIN_WAVEFORM_DURATION_S
    return max(MIN_WAVEFORM_DURATION_S, min(positives))


def _adjustment_limit_for_segment(segment: dict[str, Any]) -> float:
    current_offset = abs(_segment_offset_s(segment))
    limit = max(BASE_WAVEFORM_ADJUST_LIMIT_S, current_offset + WAVEFORM_ADJUST_PADDING_S)
    return min(MAX_WAVEFORM_ADJUST_LIMIT_S, limit)


def adjusted_spans(segment: dict[str, Any], adjustment_s: float) -> tuple[float, float, float, float]:
    duration_s = _segment_duration(segment)
    if segment.get("is_global"):
        offset_s = _segment_offset_s(segment) + adjustment_s
        reference_start_s = max(0.0, -offset_s)
        video_start_s = max(0.0, offset_s)
        return (
            reference_start_s,
            reference_start_s + duration_s,
            video_start_s,
            video_start_s + duration_s,
        )
    video_start_s = max(0.0, float(segment.get("video_start_s", 0.0)) + adjustment_s)
    video_end_s = max(video_start_s, float(segment.get("video_end_s", 0.0)) + adjustment_s)
    return (
        float(segment.get("reference_start_s", 0.0)),
        float(segment.get("reference_end_s", 0.0)),
        video_start_s,
        video_end_s,
    )


def _waveform_window_bounds(
    segment: dict[str, Any],
    lower_adjustment_s: float,
    upper_adjustment_s: float,
) -> tuple[float, float, float, float]:
    candidates = [lower_adjustment_s, 0.0, upper_adjustment_s]
    spans = [adjusted_spans(segment, adjustment) for adjustment in candidates]
    ref_start = max(0.0, min(span[0] for span in spans))
    ref_end = max(ref_start + MIN_WAVEFORM_DURATION_S, max(span[1] for span in spans))
    video_start = max(0.0, min(span[2] for span in spans))
    video_end = max(video_start + MIN_WAVEFORM_DURATION_S, max(span[3] for span in spans))
    return ref_start, ref_end, video_start, video_end


def _clean_samples(samples: np.ndarray) -> np.ndarray:
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim != 1:
        arr = np.ravel(arr)
    if arr.size == 0:
        return np.zeros(0, dtype=np.float32)
    return np.nan_to_num(arr, copy=False)


def _peak_envelope(samples: np.ndarray, point_count: int) -> np.ndarray:
    arr = _clean_samples(samples)
    if arr.size == 0 or point_count <= 0:
        return np.zeros(max(0, point_count), dtype=np.float32)
    point_count = max(1, int(point_count))
    edges = np.linspace(0, arr.size, point_count + 1, dtype=np.int64)
    envelope = np.zeros(point_count, dtype=np.float32)
    for index in range(point_count):
        start = int(edges[index])
        end = int(edges[index + 1])
        if end <= start:
            end = min(arr.size, start + 1)
        chunk = arr[start:end]
        if chunk.size:
            envelope[index] = float(np.max(np.abs(chunk)))
    peak = float(np.max(envelope)) if envelope.size else 0.0
    if peak > 1e-6:
        envelope /= peak
    return envelope
