from __future__ import annotations

import logging
from dataclasses import dataclass
from math import ceil

import numpy as np
from scipy import signal

from rhythmflow.config import FMAX, FMIN, N_FFT, SR
from rhythmflow.core.alignment import AlignmentResult, chromagram


logger = logging.getLogger(__name__)


SMART_HOP = 2048
MIN_SMART_DURATION_S = 2.0
MIN_CUT_GAP_S = 0.45
MIN_USEFUL_TRIM_S = 0.65
COVER_SAFE_MIN_CONFIDENCE = 0.62
COVER_SAFE_MIN_VIDEO_COVERAGE = 0.78
COVER_SAFE_TRIM_SLACK_S = 0.75


@dataclass(frozen=True)
class ReferenceSegment:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass(frozen=True)
class AlignmentPlan:
    video_start_s: float
    video_end_s: float
    video_segments: tuple[ReferenceSegment, ...]
    reference_segments: tuple[ReferenceSegment, ...]
    base_offset_s: float
    confidence: float
    video_trim_total_s: float
    reference_trim_total_s: float
    trim_total_s: float
    trimmed_segment_count: int
    needs_review: bool
    warnings: tuple[str, ...]
    method: str

    @property
    def duration_s(self) -> float:
        if self.video_segments:
            return sum(segment.duration_s for segment in self.video_segments)
        return max(0.0, self.video_end_s - self.video_start_s)


def build_alignment_plan(
    reference_audio: np.ndarray,
    video_audio: np.ndarray,
    base_result: AlignmentResult,
    *,
    sr: int = SR,
) -> AlignmentPlan:
    logger.info(
        "Building segmented alignment plan: base_offset=%.3f base_confidence=%.2f",
        base_result.offset_s,
        base_result.confidence,
    )
    reference = _clean_audio(reference_audio)
    video = _clean_audio(video_audio)
    ref_duration = reference.size / float(sr)
    video_duration = video.size / float(sr)
    fallback = _fallback_plan(base_result, ref_duration, video_duration)

    if min(ref_duration, video_duration) < MIN_SMART_DURATION_S:
        logger.warning(
            "Smart alignment skipped because audio is too short: ref=%.3f video=%.3f",
            ref_duration,
            video_duration,
        )
        return _with_warning(fallback, "audio_too_short", needs_review=True)
    if abs(ref_duration - video_duration) < MIN_USEFUL_TRIM_S:
        logger.info("Smart alignment skipped because durations are already close")
        return _with_warning(fallback, "no_reference_extra_duration", needs_review=fallback.needs_review)

    try:
        ref_features = _structure_features(reference, sr=sr, hop=SMART_HOP)
        video_features = _structure_features(video, sr=sr, hop=SMART_HOP)
        path = _local_alignment_path(video_features, ref_features)
    except Exception:
        logger.exception("Smart alignment failed; falling back to global alignment")
        return _with_warning(fallback, "smart_alignment_failed", needs_review=True)

    if len(path.video_frames) < _seconds_to_frames(MIN_SMART_DURATION_S, sr):
        logger.warning("Smart alignment produced no reliable common path")
        return _with_warning(fallback, "no_reliable_common_path", needs_review=True)

    plan = _plan_from_path(
        path,
        video_features,
        ref_features,
        base_result,
        ref_duration,
        video_duration,
        sr,
    )

    if plan.trim_total_s < MIN_USEFUL_TRIM_S:
        return AlignmentPlan(
            **{
                **fallback.__dict__,
                "confidence": max(fallback.confidence, plan.confidence),
                "warnings": plan.warnings,
            }
        )

    safety_warning = _cover_safe_rejection_reason(plan, ref_duration, video_duration)
    if safety_warning is not None:
        logger.warning("Smart alignment rejected by safety check: %s", safety_warning)
        return _with_warning(fallback, safety_warning, needs_review=True)

    logger.info(
        "Segmented alignment plan built: trim=%.3f segments=%d confidence=%.2f review=%s",
        plan.trim_total_s,
        plan.trimmed_segment_count,
        plan.confidence,
        plan.needs_review,
    )
    return plan


def _clean_audio(audio: np.ndarray) -> np.ndarray:
    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim != 1:
        arr = np.mean(arr, axis=0, dtype=np.float32)
    if arr.size == 0:
        raise ValueError("Audio is empty")
    return np.nan_to_num(arr, copy=False)


def _structure_features(audio: np.ndarray, *, sr: int, hop: int) -> np.ndarray:
    chroma = chromagram(audio, sr=sr, hop=hop, fmax=min(1800.0, FMAX))

    freqs, _, stft = signal.stft(
        audio,
        fs=sr,
        window="hann",
        nperseg=N_FFT,
        noverlap=N_FFT - hop,
        boundary=None,
        padded=True,
    )
    magnitude = np.abs(stft).astype(np.float32, copy=False)
    valid = (freqs >= FMIN) & (freqs <= FMAX)
    magnitude = magnitude[valid]
    freqs = freqs[valid]
    if magnitude.size == 0:
        raise ValueError("No usable spectral bins")

    frame_count = min(chroma.shape[1], magnitude.shape[1])
    chroma = chroma[:, :frame_count]
    magnitude = magnitude[:, :frame_count]

    band_energy_rows = []
    band_flux_rows = []
    band_edges = np.geomspace(max(FMIN, 40.0), min(4200.0, sr * 0.45), num=8)
    for low, high in zip(band_edges[:-1], band_edges[1:]):
        mask = (freqs >= low) & (freqs < high)
        if np.count_nonzero(mask) < 2:
            continue
        band = np.log1p(magnitude[mask])
        energy = np.mean(band, axis=0)
        flux = np.maximum(np.diff(energy, prepend=energy[:1]), 0.0)
        band_energy_rows.append(energy)
        band_flux_rows.append(flux)

    if not band_energy_rows:
        raise ValueError("No usable structure bands")

    band_energy = _robust_normalize(np.vstack(band_energy_rows))
    band_flux = _robust_normalize(np.vstack(band_flux_rows))
    broad_energy = _robust_normalize(np.mean(band_energy, axis=0, keepdims=True))
    broad_flux = _robust_normalize(np.maximum(np.diff(broad_energy, prepend=broad_energy[:, :1]), 0.0))

    rows = [
        chroma * 1.1,
        band_energy[:4] * 0.45,
        band_flux * 1.05,
        broad_energy * 0.35,
        broad_flux * 0.8,
    ]
    features = np.vstack([row[:, :frame_count] for row in rows]).astype(np.float32, copy=False)
    features -= np.mean(features, axis=1, keepdims=True)
    norms = np.linalg.norm(features, axis=0, keepdims=True)
    return np.divide(features, norms + 1e-8, out=np.zeros_like(features), where=norms > 0)


def _robust_normalize(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    median = np.median(arr, axis=1, keepdims=True)
    mad = np.median(np.abs(arr - median), axis=1, keepdims=True)
    scale = np.maximum(1.4826 * mad, np.std(arr, axis=1, keepdims=True))
    return (arr - median) / (scale + 1e-6)


@dataclass(frozen=True)
class _AlignmentPath:
    video_frames: np.ndarray
    reference_frames: np.ndarray


def _local_alignment_path(video_features: np.ndarray, ref_features: np.ndarray) -> _AlignmentPath:
    video = _validate_features(video_features, "video")
    reference = _validate_features(ref_features, "reference")
    video_count = video.shape[1]
    ref_count = reference.shape[1]
    trace_match = np.zeros((video_count + 1, ref_count + 1), dtype=np.uint8)
    trace_video_gap = np.zeros((video_count + 1, ref_count + 1), dtype=np.uint8)
    trace_ref_gap = np.zeros((video_count + 1, ref_count + 1), dtype=np.uint8)
    match_previous = np.zeros(ref_count + 1, dtype=np.float32)
    match_current = np.zeros(ref_count + 1, dtype=np.float32)
    video_gap_previous = np.zeros(ref_count + 1, dtype=np.float32)
    video_gap_current = np.zeros(ref_count + 1, dtype=np.float32)
    ref_gap_previous = np.zeros(ref_count + 1, dtype=np.float32)
    ref_gap_current = np.zeros(ref_count + 1, dtype=np.float32)
    best_score = 0.0
    best_pos = (0, 0, 0)

    gap_open = 1.05
    gap_extend = 0.025
    for i in range(1, video_count + 1):
        match_current.fill(0.0)
        video_gap_current.fill(0.0)
        ref_gap_current.fill(0.0)
        sim = np.dot(video[:, i - 1], reference).astype(np.float32, copy=False)
        match_scores = sim * 2.0 - 0.62
        for j in range(1, ref_count + 1):
            diag_source, diag_base = _best_source(
                (
                    float(match_previous[j - 1]),
                    float(video_gap_previous[j - 1]),
                    float(ref_gap_previous[j - 1]),
                )
            )
            match_score = diag_base + float(match_scores[j - 1])
            if match_score > 0.0:
                match_current[j] = match_score
                trace_match[i, j] = diag_source

            video_source, video_gap_score = _best_source(
                (
                    float(match_previous[j]) - gap_open,
                    float(video_gap_previous[j]) - gap_extend,
                    float(ref_gap_previous[j]) - gap_open,
                )
            )
            if video_gap_score > 0.0:
                video_gap_current[j] = video_gap_score
                trace_video_gap[i, j] = video_source

            ref_source, ref_gap_score = _best_source(
                (
                    float(match_current[j - 1]) - gap_open,
                    float(video_gap_current[j - 1]) - gap_open,
                    float(ref_gap_current[j - 1]) - gap_extend,
                )
            )
            if ref_gap_score > 0.0:
                ref_gap_current[j] = ref_gap_score
                trace_ref_gap[i, j] = ref_source

            for state, score in (
                (1, match_current[j]),
                (2, video_gap_current[j]),
                (3, ref_gap_current[j]),
            ):
                if score > best_score:
                    best_score = float(score)
                    best_pos = (i, j, state)
        match_previous, match_current = match_current, match_previous
        video_gap_previous, video_gap_current = video_gap_current, video_gap_previous
        ref_gap_previous, ref_gap_current = ref_gap_current, ref_gap_previous

    video_frames: list[int] = []
    ref_frames: list[int] = []
    i, j, state = best_pos
    while i > 0 and j > 0 and state:
        if state == 1:
            next_state = int(trace_match[i, j])
            if next_state == 0:
                break
            video_frames.append(i - 1)
            ref_frames.append(j - 1)
            i -= 1
            j -= 1
            state = next_state
            continue
        if state == 2:
            next_state = int(trace_video_gap[i, j])
            if next_state == 0:
                break
            i -= 1
            state = next_state
            continue
        next_state = int(trace_ref_gap[i, j])
        if next_state == 0:
            break
        j -= 1
        state = next_state

    video_frames.reverse()
    ref_frames.reverse()
    return _AlignmentPath(
        video_frames=np.asarray(video_frames, dtype=np.int32),
        reference_frames=np.asarray(ref_frames, dtype=np.int32),
    )


def _best_source(scores: tuple[float, float, float]) -> tuple[int, float]:
    best_index = int(np.argmax(scores))
    best_score = scores[best_index]
    if best_score <= 0.0:
        return 0, 0.0
    return best_index + 1, best_score


def _validate_features(features: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(features, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[1] < 2:
        raise ValueError(f"{name} features must have at least two frames")
    return np.nan_to_num(arr, copy=False)


def _plan_from_path(
    path: _AlignmentPath,
    video_features: np.ndarray,
    ref_features: np.ndarray,
    base_result: AlignmentResult,
    ref_duration: float,
    video_duration: float,
    sr: int,
) -> AlignmentPlan:
    video_frames = path.video_frames
    ref_frames = path.reference_frames
    frame_s = SMART_HOP / float(sr)
    cut_gap_frames = max(2, ceil(MIN_CUT_GAP_S / frame_s))

    video_segments: list[ReferenceSegment] = []
    reference_segments: list[ReferenceSegment] = []
    video_segment_start = int(video_frames[0])
    reference_segment_start = int(ref_frames[0])
    last_ref = int(ref_frames[0])
    last_video = int(video_frames[0])
    warnings: list[str] = []
    for video_frame, ref_frame in zip(video_frames[1:], ref_frames[1:]):
        video_gap = int(video_frame) - last_video
        ref_gap = int(ref_frame) - last_ref
        extra_ref_gap = ref_gap - max(1, video_gap)
        extra_video_gap = video_gap - max(1, ref_gap)
        if extra_ref_gap >= cut_gap_frames or extra_video_gap >= cut_gap_frames:
            video_segments.append(_segment_from_frames(video_segment_start, last_video + 1, video_duration, frame_s))
            reference_segments.append(
                _segment_from_frames(reference_segment_start, last_ref + 1, ref_duration, frame_s)
            )
            video_segment_start = int(video_frame)
            reference_segment_start = int(ref_frame)
        last_video = int(video_frame)
        last_ref = int(ref_frame)
    video_segments.append(_segment_from_frames(video_segment_start, last_video + 1, video_duration, frame_s))
    reference_segments.append(_segment_from_frames(reference_segment_start, last_ref + 1, ref_duration, frame_s))
    video_segments_tuple, reference_segments_tuple = _merge_short_segment_pairs(
        tuple(video_segments),
        tuple(reference_segments),
        video_duration,
        ref_duration,
    )

    video_kept_duration = sum(segment.duration_s for segment in video_segments_tuple)
    reference_kept_duration = sum(segment.duration_s for segment in reference_segments_tuple)
    video_start = video_segments_tuple[0].start_s
    video_end = video_segments_tuple[-1].end_s
    video_trim_total = max(0.0, video_duration - video_kept_duration)
    reference_trim_total = max(0.0, ref_duration - reference_kept_duration)
    trim_total = video_trim_total + reference_trim_total

    similarities = np.sum(
        video_features[:, video_frames] * ref_features[:, ref_frames],
        axis=0,
    )
    mean_similarity = float(np.mean((similarities + 1.0) * 0.5))
    common_kept_duration = min(video_kept_duration, reference_kept_duration)
    coverage = min(1.0, common_kept_duration / max(1e-6, min(ref_duration, video_duration)))
    confidence = float(np.clip(0.7 * mean_similarity + 0.3 * coverage, 0.0, 1.0))

    if reference_trim_total > 0.0 and not _has_internal_trim(reference_segments_tuple, ref_duration):
        warnings.append("only_reference_edge_trim_detected")
    if video_trim_total > 0.0 and not _has_internal_trim(video_segments_tuple, video_duration):
        warnings.append("only_video_edge_trim_detected")
    if max(len(reference_segments_tuple), len(video_segments_tuple)) > 4:
        warnings.append("many_alignment_segments")

    needs_review = confidence < 0.55 or max(len(reference_segments_tuple), len(video_segments_tuple)) > 4
    return AlignmentPlan(
        video_start_s=video_start,
        video_end_s=video_end,
        video_segments=video_segments_tuple,
        reference_segments=reference_segments_tuple,
        base_offset_s=base_result.offset_s,
        confidence=confidence,
        video_trim_total_s=video_trim_total,
        reference_trim_total_s=reference_trim_total,
        trim_total_s=trim_total,
        trimmed_segment_count=(
            _trim_interval_count(reference_segments_tuple, ref_duration)
            + _trim_interval_count(video_segments_tuple, video_duration)
        ),
        needs_review=needs_review,
        warnings=tuple(warnings),
        method="segmented",
    )


def _cover_safe_rejection_reason(
    plan: AlignmentPlan,
    ref_duration: float,
    video_duration: float,
) -> str | None:
    expected_reference_extra = max(0.0, ref_duration - video_duration)
    expected_video_extra = max(0.0, video_duration - ref_duration)
    video_kept_duration = sum(segment.duration_s for segment in plan.video_segments)
    reference_kept_duration = sum(segment.duration_s for segment in plan.reference_segments)
    common_kept_duration = min(video_kept_duration, reference_kept_duration)
    shared_coverage = common_kept_duration / max(min(ref_duration, video_duration), 1e-6)
    if plan.confidence < COVER_SAFE_MIN_CONFIDENCE:
        return "cover_safe_low_confidence"
    if shared_coverage < COVER_SAFE_MIN_VIDEO_COVERAGE:
        return "cover_safe_low_video_coverage"
    if plan.reference_trim_total_s > expected_reference_extra + COVER_SAFE_TRIM_SLACK_S:
        return "cover_safe_excessive_reference_trim"
    if plan.video_trim_total_s > expected_video_extra + COVER_SAFE_TRIM_SLACK_S:
        return "cover_safe_excessive_video_trim"
    return None


def _segment_from_frames(
    start_frame: int,
    end_frame: int,
    ref_duration: float,
    frame_s: float,
) -> ReferenceSegment:
    start = min(ref_duration, max(0.0, start_frame * frame_s))
    end = min(ref_duration, max(start, end_frame * frame_s))
    return ReferenceSegment(start, end)


def _merge_short_segment_pairs(
    video_segments: tuple[ReferenceSegment, ...],
    reference_segments: tuple[ReferenceSegment, ...],
    video_duration: float,
    ref_duration: float,
) -> tuple[tuple[ReferenceSegment, ...], tuple[ReferenceSegment, ...]]:
    pairs = [
        (video_segment, reference_segment)
        for video_segment, reference_segment in zip(video_segments, reference_segments)
        if min(video_segment.duration_s, reference_segment.duration_s) >= SMART_HOP / SR
    ]
    if not pairs:
        return (
            (ReferenceSegment(0.0, video_duration),),
            (ReferenceSegment(0.0, ref_duration),),
        )
    video_result, reference_result = zip(*pairs)
    return tuple(video_result), tuple(reference_result)


def _has_internal_trim(segments: tuple[ReferenceSegment, ...], ref_duration: float) -> bool:
    if len(segments) > 1:
        return True
    if not segments:
        return False
    segment = segments[0]
    return segment.start_s > MIN_CUT_GAP_S and ref_duration - segment.end_s > MIN_CUT_GAP_S


def _trim_interval_count(segments: tuple[ReferenceSegment, ...], ref_duration: float) -> int:
    if not segments:
        return 0
    count = int(segments[0].start_s > MIN_CUT_GAP_S)
    for left, right in zip(segments, segments[1:]):
        count += int(right.start_s - left.end_s > MIN_CUT_GAP_S)
    count += int(ref_duration - segments[-1].end_s > MIN_CUT_GAP_S)
    return count


def _fallback_plan(
    base_result: AlignmentResult,
    ref_duration: float,
    video_duration: float,
) -> AlignmentPlan:
    video_start = max(0.0, base_result.offset_s)
    reference_start = max(0.0, -base_result.offset_s)
    duration = min(ref_duration - reference_start, video_duration - video_start)
    duration = max(0.0, duration)
    return AlignmentPlan(
        video_start_s=video_start,
        video_end_s=video_start + duration,
        video_segments=(ReferenceSegment(video_start, video_start + duration),),
        reference_segments=(ReferenceSegment(reference_start, reference_start + duration),),
        base_offset_s=base_result.offset_s,
        confidence=min(1.0, max(0.0, base_result.confidence / 10.0)),
        video_trim_total_s=0.0,
        reference_trim_total_s=0.0,
        trim_total_s=0.0,
        trimmed_segment_count=0,
        needs_review=base_result.confidence < 2.0,
        warnings=(),
        method="global",
    )


def _with_warning(plan: AlignmentPlan, warning: str, *, needs_review: bool) -> AlignmentPlan:
    return AlignmentPlan(
        video_start_s=plan.video_start_s,
        video_end_s=plan.video_end_s,
        video_segments=plan.video_segments,
        reference_segments=plan.reference_segments,
        base_offset_s=plan.base_offset_s,
        confidence=plan.confidence,
        video_trim_total_s=plan.video_trim_total_s,
        reference_trim_total_s=plan.reference_trim_total_s,
        trim_total_s=plan.trim_total_s,
        trimmed_segment_count=plan.trimmed_segment_count,
        needs_review=needs_review,
        warnings=(*plan.warnings, warning),
        method=plan.method,
    )


def _seconds_to_frames(value: float, sr: int) -> int:
    return max(1, int(round(value * sr / SMART_HOP)))
