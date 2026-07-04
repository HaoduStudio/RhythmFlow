from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Callable

from rhythmflow.config import SR
from rhythmflow.core.alignment import chromagram, find_offset
from rhythmflow.core.audio_io import decode_mono
from rhythmflow.core.ffmpeg_tools import probe
from rhythmflow.core.pipeline import ProcessJob, process_video
from rhythmflow.core.segmented_alignment import AlignmentPlan, MIN_USEFUL_TRIM_S, build_alignment_plan
from rhythmflow.logging_setup import record_metric
from rhythmflow.webui.events import Emitter

logger = logging.getLogger(__name__)

ResultCallback = Callable[[int, dict[str, Any]], None]


def run_analysis(
    videos: list[str],
    reference_audio: str,
    language: str,
    emitter: Emitter,
    on_result: ResultCallback,
) -> None:
    logger.info("Analysis started for %d video(s); reference=%s", len(videos), reference_audio)
    record_metric("rhythmflow.analysis.started", 1)
    emitter.emit("log", _log(language, "starting_analysis"))
    try:
        emitter.emit("log", _log(language, "decoding_reference"))
        ref_audio = decode_mono(reference_audio, SR)
        ref_chroma = chromagram(ref_audio)
        ref_duration = ref_audio.size / float(SR)
        total = max(1, len(videos))
        for index, video_path in enumerate(videos):
            try:
                emitter.emit("log", _log(language, "analyzing_path", path=video_path))
                media = probe(video_path)
                if not media.has_audio:
                    raise RuntimeError(_log(language, "video_no_audio"))
                video_audio = decode_mono(video_path, SR)
                result = find_offset(ref_chroma, chromagram(video_audio))
                plan: AlignmentPlan | None = None
                video_duration = video_audio.size / float(SR)
                if _should_run_smart_analysis(ref_duration, video_duration):
                    emitter.emit("log", _log(language, "smart_analyzing", path=video_path))
                    plan = build_alignment_plan(ref_audio, video_audio, result, sr=SR)
                data = {
                    "path": video_path,
                    "ok": True,
                    **asdict(result),
                    **_plan_payload(plan, base_needs_review=result.confidence < 2.0),
                }
                on_result(index, data)
                emitter.emit(
                    "log",
                    _log(
                        language,
                        "detected_result",
                        offset=f"{result.offset_s:.3f}",
                        confidence=f"{result.confidence:.2f}",
                        path=video_path,
                    ),
                )
                record_metric("rhythmflow.analysis.video.completed", 1)
                if plan is not None and plan.trimmed_segment_count > 0:
                    emitter.emit(
                        "log",
                        _log(
                            language,
                            "smart_result",
                            trim=f"{plan.trim_total_s:.2f}",
                            count=str(plan.trimmed_segment_count),
                            confidence=f"{plan.confidence:.2f}",
                            path=video_path,
                        ),
                    )
            except Exception as exc:  # noqa: BLE001 - report per-file failures
                logger.exception("Analysis failed for %s", video_path)
                record_metric("rhythmflow.analysis.video.failed", 1)
                on_result(index, {"path": video_path, "ok": False, "error": str(exc)})
                emitter.emit("error", f"{video_path}: {exc}")
            finally:
                emitter.emit("progress", int((index + 1) / total * 100))
    except Exception as exc:  # noqa: BLE001 - report the whole pass failing
        logger.exception("Analysis pass failed")
        record_metric("rhythmflow.analysis.failed", 1)
        emitter.emit("error", str(exc))
    finally:
        logger.info("Analysis pass finished")
        emitter.emit("analyze_done", {})


def run_processing(jobs: list[ProcessJob], language: str, emitter: Emitter) -> None:
    total = max(1, len(jobs))
    logger.info("Processing started for %d job(s)", len(jobs))
    record_metric("rhythmflow.processing.started", 1)
    emitter.emit("log", _log(language, "starting_processing"))
    try:
        for index, job in enumerate(jobs):
            emitter.emit("file_started", job.video_path)
            emitter.emit("log", _log(language, "processing_path", path=job.video_path))

            def on_file_progress(value: int, _index: int = index) -> None:
                emitter.emit("progress", int((_index + value / 100) / total * 100))

            try:
                output_path = process_video(job, on_progress=on_file_progress)
                emitter.emit("file_finished", {"video_path": job.video_path, "output_path": output_path})
                emitter.emit("log", _log(language, "output_path", path=output_path))
                emitter.emit("log", _log(language, "finished_path", path=output_path))
                record_metric("rhythmflow.processing.video.completed", 1)
            except Exception as exc:  # noqa: BLE001 - report per-file failures
                logger.exception("Processing failed for %s", job.video_path)
                record_metric("rhythmflow.processing.video.failed", 1)
                emitter.emit("error", f"{job.video_path}: {exc}")
            finally:
                emitter.emit("progress", int((index + 1) / total * 100))
    finally:
        logger.info("Processing pass finished")
        emitter.emit("process_done", {})


def _should_run_smart_analysis(ref_duration: float, video_duration: float) -> bool:
    return abs(ref_duration - video_duration) >= MIN_USEFUL_TRIM_S


def _plan_payload(plan: AlignmentPlan | None, *, base_needs_review: bool = False) -> dict[str, Any]:
    if plan is None:
        return {
            "smart_trim_s": 0.0,
            "smart_trim_count": 0,
            "smart_confidence": None,
            "needs_review": base_needs_review,
            "alignment_plan": None,
        }
    return {
        "smart_trim_s": plan.trim_total_s,
        "smart_trim_count": plan.trimmed_segment_count,
        "smart_confidence": plan.confidence,
        "needs_review": plan.needs_review,
        "alignment_plan": {
            "method": plan.method,
            "video_start_s": plan.video_start_s,
            "video_end_s": plan.video_end_s,
            "duration_s": plan.duration_s,
            "video_segments": [
                {"start_s": segment.start_s, "end_s": segment.end_s}
                for segment in plan.video_segments
            ],
            "reference_segments": [
                {"start_s": segment.start_s, "end_s": segment.end_s}
                for segment in plan.reference_segments
            ],
            "video_trim_total_s": plan.video_trim_total_s,
            "reference_trim_total_s": plan.reference_trim_total_s,
            "trim_total_s": plan.trim_total_s,
            "trimmed_segment_count": plan.trimmed_segment_count,
            "confidence": plan.confidence,
            "needs_review": plan.needs_review,
            "warnings": list(plan.warnings),
        },
    }


# Log strings that stay on the Python side (ported from ui/workers.py and the
# main-window log calls). UI labels live in the front-end i18n table instead.
_LOG_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "starting_analysis": "Starting analysis...",
        "starting_processing": "Starting processing...",
        "decoding_reference": "Decoding reference audio...",
        "analyzing_path": "Analyzing {path}",
        "video_no_audio": "Video has no audio track, so it cannot be aligned automatically",
        "detected_result": "Detected {offset}s, confidence {confidence}: {path}",
        "smart_analyzing": "Running smart segment analysis: {path}",
        "smart_result": "Smart trim {trim}s in {count} segment(s), AI confidence {confidence}: {path}",
        "processing_path": "Processing {path}",
        "output_path": "Output: {path}",
        "finished_path": "Finished {path}",
    },
    "zh": {
        "starting_analysis": "开始分析...",
        "starting_processing": "开始处理...",
        "decoding_reference": "正在解码参考音频...",
        "analyzing_path": "正在分析 {path}",
        "video_no_audio": "视频没有音频轨道，无法自动对齐",
        "detected_result": "检测到 {offset}s，置信度 {confidence}：{path}",
        "smart_analyzing": "正在进行智能分段分析：{path}",
        "smart_result": "智能裁掉 {trim}s，共 {count} 段，AI 置信度 {confidence}：{path}",
        "processing_path": "正在处理 {path}",
        "output_path": "输出：{path}",
        "finished_path": "已完成 {path}",
    },
}


def _log(language: str, key: str, **kwargs: object) -> str:
    strings = _LOG_STRINGS.get(language, _LOG_STRINGS["zh"])
    template = strings.get(key, _LOG_STRINGS["en"].get(key, key))
    return template.format(**kwargs) if kwargs else template
