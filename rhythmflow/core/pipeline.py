from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from rhythmflow.core.segmented_alignment import ReferenceSegment

from .ffmpeg_tools import FfmpegError, format_seconds, probe, run_ffmpeg


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CutWindow:
    video_start: float
    reference_start: float
    duration: float


@dataclass(frozen=True)
class ProcessJob:
    video_path: str
    reference_audio_path: str
    output_path: str
    offset_s: float
    original_volume: float
    reference_volume: float
    mode: str = "accurate"
    video_start_s: float | None = None
    duration_s: float | None = None
    video_segments: tuple[ReferenceSegment, ...] = ()
    reference_segments: tuple[ReferenceSegment, ...] = ()


def compute_cut_window(offset_s: float, ref_duration_s: float, video_duration_s: float) -> CutWindow:
    logger.debug(
        "Computing cut window: offset=%.3f ref_duration=%.3f video_duration=%.3f",
        offset_s,
        ref_duration_s,
        video_duration_s,
    )
    video_start = max(0.0, offset_s)
    reference_start = max(0.0, -offset_s)
    duration = min(ref_duration_s - reference_start, video_duration_s - video_start)
    if duration <= 0:
        logger.error(
            "No media overlap after offset: offset=%.3f ref_duration=%.3f video_duration=%.3f",
            offset_s,
            ref_duration_s,
            video_duration_s,
        )
        raise ValueError(
            "The reference and video do not overlap after applying the selected offset"
        )
    return CutWindow(
        video_start=video_start,
        reference_start=reference_start,
        duration=duration,
    )


def build_output_path(video_path: str, output_dir: str, pattern: str, index: int = 1) -> str:
    video = Path(video_path)
    safe_name = _safe_filename(video.stem)
    pattern = pattern.strip() or "{name}_aligned.mp4"
    filename = pattern.format(
        name=safe_name,
        stem=safe_name,
        index=index,
        ext=video.suffix.lstrip(".") or "mp4",
    )
    if Path(filename).suffix.lower() != ".mp4":
        filename += ".mp4"
    output_path = str(Path(output_dir).expanduser().resolve() / filename)
    logger.info("Built output path for %s: %s", video_path, output_path)
    return output_path


def process_video(
    job: ProcessJob,
    *,
    on_progress: Callable[[int], None] | None = None,
) -> str:
    logger.info(
        "Processing video: video=%s reference=%s output=%s mode=%s offset=%.3f",
        job.video_path,
        job.reference_audio_path,
        job.output_path,
        job.mode,
        job.offset_s,
    )
    video_info = probe(job.video_path)
    ref_info = probe(job.reference_audio_path)
    if not video_info.has_video:
        raise FfmpegError(f"Input has no video stream: {job.video_path}")
    if ref_info.duration <= 0:
        raise FfmpegError(f"Reference duration could not be detected: {job.reference_audio_path}")
    if video_info.duration <= 0:
        raise FfmpegError(f"Video duration could not be detected: {job.video_path}")

    window, video_segments, reference_segments = _job_window(job, ref_info.duration, video_info.duration)
    logger.info(
        "Process window: video_start=%.3f reference_start=%.3f duration=%.3f video_segments=%d reference_segments=%d",
        window.video_start,
        window.reference_start,
        window.duration,
        len(video_segments),
        len(reference_segments),
    )
    effective_job = replace(
        job,
        video_start_s=window.video_start,
        duration_s=window.duration,
        video_segments=video_segments,
        reference_segments=reference_segments,
    )
    args = build_ffmpeg_args(effective_job, window, include_original_audio=video_info.has_audio)
    output_path = Path(job.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(args, duration_s=window.duration, on_progress=on_progress)
    logger.info("Video processed successfully: %s", output_path)
    return str(output_path)


def build_ffmpeg_args(
    job: ProcessJob,
    window: CutWindow,
    *,
    include_original_audio: bool = True,
) -> list[str]:
    if job.mode not in {"accurate", "fast"}:
        logger.error("Unknown cut mode requested: %s", job.mode)
        raise ValueError(f"Unknown cut mode: {job.mode}")

    if _requires_video_filter(job.video_segments):
        logger.debug("Building segmented ffmpeg args for %s", job.video_path)
        return _build_segmented_video_args(job, include_original_audio=include_original_audio)

    original_volume = max(0.0, float(job.original_volume))
    reference_volume = max(0.0, float(job.reference_volume))
    filter_complex, audio_map = _audio_filter(
        original_volume,
        reference_volume,
        include_original_audio=include_original_audio,
        reference_segments=job.reference_segments,
    )

    args = [
        "-y",
        "-ss",
        format_seconds(window.video_start),
        "-i",
        job.video_path,
    ]
    if job.reference_segments:
        args.extend(["-i", job.reference_audio_path])
    else:
        args.extend(
            [
                "-ss",
                format_seconds(window.reference_start),
                "-i",
                job.reference_audio_path,
            ]
        )
    args.extend(
        [
            "-t",
            format_seconds(window.duration),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            audio_map,
        ]
    )

    if job.mode == "fast":
        args.extend(
            [
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-avoid_negative_ts",
                "make_zero",
            ]
        )
    else:
        args.extend(
            [
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )

    args.append(job.output_path)
    logger.debug("Built ffmpeg args for %s", job.video_path)
    return args


def _build_segmented_video_args(
    job: ProcessJob,
    *,
    include_original_audio: bool,
) -> list[str]:
    original_volume = max(0.0, float(job.original_volume))
    reference_volume = max(0.0, float(job.reference_volume))
    filter_complex, audio_map = _segmented_media_filter(
        job.video_segments,
        job.reference_segments,
        original_volume,
        reference_volume,
        include_original_audio=include_original_audio,
    )
    args = [
        "-y",
        "-i",
        job.video_path,
        "-i",
        job.reference_audio_path,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        audio_map,
    ]
    if job.mode == "fast":
        args.extend(
            [
                "-c:v",
                "libx264",
                "-crf",
                "20",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    else:
        args.extend(
            [
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
            ]
        )
    args.append(job.output_path)
    return args


def _job_window(
    job: ProcessJob,
    ref_duration_s: float,
    video_duration_s: float,
) -> tuple[CutWindow, tuple[ReferenceSegment, ...], tuple[ReferenceSegment, ...]]:
    if not job.reference_segments:
        return compute_cut_window(job.offset_s, ref_duration_s, video_duration_s), (), ()

    if job.video_segments:
        video_segments = _fit_video_segments(job.video_segments, video_duration_s)
        reference_segments = _fit_reference_segments(job.reference_segments, ref_duration_s)
        paired_video_segments, paired_reference_segments = _fit_paired_segments(
            video_segments,
            reference_segments,
        )
        duration = sum(segment.duration_s for segment in paired_video_segments)
        if duration <= 0:
            raise ValueError(
                "Alignment plan contains no usable paired video/reference segments"
            )
        if _requires_video_filter(paired_video_segments):
            return (
                CutWindow(
                    video_start=paired_video_segments[0].start_s,
                    reference_start=paired_reference_segments[0].start_s,
                    duration=duration,
                ),
                paired_video_segments,
                paired_reference_segments,
            )
        return (
            CutWindow(
                video_start=paired_video_segments[0].start_s,
                reference_start=0.0,
                duration=duration,
            ),
            (),
            paired_reference_segments,
        )

    video_start = max(0.0, job.video_start_s if job.video_start_s is not None else job.offset_s)
    available_video = max(0.0, video_duration_s - video_start)
    reference_segments = _fit_reference_segments(job.reference_segments, ref_duration_s)
    reference_duration = sum(segment.duration_s for segment in reference_segments)
    requested_duration = job.duration_s if job.duration_s is not None else reference_duration
    duration = min(max(0.0, requested_duration), available_video, reference_duration)
    if duration <= 0:
        raise ValueError(
            "The reference and video do not overlap after applying the selected alignment plan"
        )
    fitted_segments = _limit_segments_to_duration(reference_segments, duration)
    return CutWindow(video_start=video_start, reference_start=0.0, duration=duration), (), fitted_segments


def _fit_reference_segments(
    segments: tuple[ReferenceSegment, ...],
    ref_duration_s: float,
) -> tuple[ReferenceSegment, ...]:
    return _fit_segments(
        segments,
        ref_duration_s,
        "Alignment plan contains no usable reference segments",
    )


def _fit_video_segments(
    segments: tuple[ReferenceSegment, ...],
    video_duration_s: float,
) -> tuple[ReferenceSegment, ...]:
    return _fit_segments(
        segments,
        video_duration_s,
        "Alignment plan contains no usable video segments",
    )


def _fit_segments(
    segments: tuple[ReferenceSegment, ...],
    duration_s: float,
    empty_message: str,
) -> tuple[ReferenceSegment, ...]:
    fitted: list[ReferenceSegment] = []
    for segment in segments:
        start = max(0.0, min(float(segment.start_s), duration_s))
        end = max(start, min(float(segment.end_s), duration_s))
        if end - start > 0.001:
            fitted.append(ReferenceSegment(start, end))
    if not fitted:
        raise ValueError(empty_message)
    return tuple(fitted)


def _fit_paired_segments(
    video_segments: tuple[ReferenceSegment, ...],
    reference_segments: tuple[ReferenceSegment, ...],
) -> tuple[tuple[ReferenceSegment, ...], tuple[ReferenceSegment, ...]]:
    fitted_video: list[ReferenceSegment] = []
    fitted_reference: list[ReferenceSegment] = []
    for video_segment, reference_segment in zip(video_segments, reference_segments):
        duration = min(video_segment.duration_s, reference_segment.duration_s)
        if duration <= 0.001:
            continue
        fitted_video.append(
            ReferenceSegment(video_segment.start_s, video_segment.start_s + duration)
        )
        fitted_reference.append(
            ReferenceSegment(reference_segment.start_s, reference_segment.start_s + duration)
        )
    if not fitted_video or not fitted_reference:
        raise ValueError("Alignment plan contains no usable paired segments")
    return tuple(fitted_video), tuple(fitted_reference)


def _limit_segments_to_duration(
    segments: tuple[ReferenceSegment, ...],
    duration_s: float,
) -> tuple[ReferenceSegment, ...]:
    remaining = max(0.0, duration_s)
    limited: list[ReferenceSegment] = []
    for segment in segments:
        if remaining <= 0:
            break
        take = min(segment.duration_s, remaining)
        if take > 0.001:
            limited.append(ReferenceSegment(segment.start_s, segment.start_s + take))
            remaining -= take
    return tuple(limited)


def _audio_filter(
    original_volume: float,
    reference_volume: float,
    *,
    include_original_audio: bool,
    reference_segments: tuple[ReferenceSegment, ...] = (),
) -> tuple[str, str]:
    ref_parts, reference_label = _reference_filter_parts(reference_segments)
    if not include_original_audio or original_volume <= 0:
        ref_parts.append(f"{reference_label}volume={reference_volume:.4f}[aout]")
        return ";".join(ref_parts), "[aout]"
    if reference_volume <= 0:
        return f"[0:a]volume={original_volume:.4f}[aout]", "[aout]"
    ref_parts.extend(
        [
            f"[0:a]volume={original_volume:.4f}[a0]",
            f"{reference_label}volume={reference_volume:.4f}[a1]",
            "[a0][a1]amix=inputs=2:duration=longest:normalize=0[aout]",
        ]
    )
    return ";".join(ref_parts), "[aout]"


def _reference_filter_parts(
    reference_segments: tuple[ReferenceSegment, ...],
) -> tuple[list[str], str]:
    if not reference_segments:
        return [], "[1:a]"
    parts: list[str] = []
    labels: list[str] = []
    for index, segment in enumerate(reference_segments):
        label = f"[r{index}]"
        labels.append(label)
        parts.append(
            f"[1:a]atrim=start={format_seconds(segment.start_s)}:"
            f"end={format_seconds(segment.end_s)},asetpts=PTS-STARTPTS{label}"
        )
    if len(labels) == 1:
        return parts, labels[0]
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[refcat]")
    return parts, "[refcat]"


def _segmented_media_filter(
    video_segments: tuple[ReferenceSegment, ...],
    reference_segments: tuple[ReferenceSegment, ...],
    original_volume: float,
    reference_volume: float,
    *,
    include_original_audio: bool,
) -> tuple[str, str]:
    if not video_segments or len(video_segments) != len(reference_segments):
        raise ValueError("Video and reference segment counts must match")

    parts: list[str] = []
    video_labels: list[str] = []
    original_audio_labels: list[str] = []
    reference_labels: list[str] = []
    use_original_audio = include_original_audio and original_volume > 0
    use_reference_audio = reference_volume > 0 or not use_original_audio

    for index, (video_segment, reference_segment) in enumerate(zip(video_segments, reference_segments)):
        video_label = f"[v{index}]"
        video_labels.append(video_label)
        parts.append(
            f"[0:v]trim=start={format_seconds(video_segment.start_s)}:"
            f"end={format_seconds(video_segment.end_s)},setpts=PTS-STARTPTS{video_label}"
        )

        if use_original_audio:
            original_label = f"[oa{index}]"
            original_audio_labels.append(original_label)
            parts.append(
                f"[0:a]atrim=start={format_seconds(video_segment.start_s)}:"
                f"end={format_seconds(video_segment.end_s)},asetpts=PTS-STARTPTS{original_label}"
            )

        if use_reference_audio:
            reference_label = f"[ra{index}]"
            reference_labels.append(reference_label)
            parts.append(
                f"[1:a]atrim=start={format_seconds(reference_segment.start_s)}:"
                f"end={format_seconds(reference_segment.end_s)},asetpts=PTS-STARTPTS{reference_label}"
            )

    video_label = _concat_labels(parts, video_labels, output="vcat", video=1, audio=0)
    parts.append(f"{video_label}null[vout]")

    original_label = (
        _concat_labels(parts, original_audio_labels, output="oacat", video=0, audio=1)
        if original_audio_labels
        else None
    )
    reference_label = (
        _concat_labels(parts, reference_labels, output="refcat", video=0, audio=1)
        if reference_labels
        else None
    )

    if original_label is None:
        if reference_label is None:
            raise ValueError("Segmented output needs at least one audio source")
        parts.append(f"{reference_label}volume={reference_volume:.4f}[aout]")
        return ";".join(parts), "[aout]"
    if reference_label is None or reference_volume <= 0:
        parts.append(f"{original_label}volume={original_volume:.4f}[aout]")
        return ";".join(parts), "[aout]"

    parts.extend(
        [
            f"{original_label}volume={original_volume:.4f}[a0]",
            f"{reference_label}volume={reference_volume:.4f}[a1]",
            "[a0][a1]amix=inputs=2:duration=longest:normalize=0[aout]",
        ]
    )
    return ";".join(parts), "[aout]"


def _concat_labels(
    parts: list[str],
    labels: list[str],
    *,
    output: str,
    video: int,
    audio: int,
) -> str:
    if not labels:
        raise ValueError("Cannot concatenate an empty segment list")
    if len(labels) == 1:
        return labels[0]
    label = f"[{output}]"
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v={video}:a={audio}{label}")
    return label


def _requires_video_filter(segments: tuple[ReferenceSegment, ...]) -> bool:
    if len(segments) <= 1:
        return False
    return any(
        right.start_s - left.end_s > 0.001
        for left, right in zip(segments, segments[1:])
    )


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")
    return cleaned or "video"
