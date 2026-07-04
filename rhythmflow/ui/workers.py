from __future__ import annotations

import logging
from dataclasses import asdict

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from rhythmflow.config import SR
from rhythmflow.core.alignment import chromagram, find_offset
from rhythmflow.core.audio_io import decode_mono
from rhythmflow.core.ffmpeg_tools import probe
from rhythmflow.core.pipeline import ProcessJob, process_video
from rhythmflow.core.segmented_alignment import AlignmentPlan, MIN_USEFUL_TRIM_S, build_alignment_plan
from rhythmflow.logging_setup import record_metric
from rhythmflow.ui.i18n import tr


logger = logging.getLogger(__name__)


class AnalyzeWorker(QObject):
    progress = pyqtSignal(int)
    result = pyqtSignal(int, object)
    log = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, videos: list[str], reference_audio: str, language: str = "zh") -> None:
        super().__init__()
        self.videos = videos
        self.reference_audio = reference_audio
        self.language = language

    @pyqtSlot()
    def run(self) -> None:
        logger.info(
            "Analysis worker started for %d video(s); reference=%s",
            len(self.videos),
            self.reference_audio,
        )
        record_metric("rhythmflow.analysis.started", 1)
        try:
            self.log.emit(_worker_tr(self.language, "decoding_reference"))
            ref_audio = decode_mono(self.reference_audio, SR)
            ref_chroma = chromagram(ref_audio)
            ref_duration = ref_audio.size / float(SR)
            total = max(1, len(self.videos))
            for index, video_path in enumerate(self.videos):
                try:
                    logger.info("Analyzing video %d/%d: %s", index + 1, total, video_path)
                    self.log.emit(_worker_tr(self.language, "analyzing_path", path=video_path))
                    media = probe(video_path)
                    if not media.has_audio:
                        raise RuntimeError(_worker_tr(self.language, "video_no_audio"))
                    video_audio = decode_mono(video_path, SR)
                    result = find_offset(ref_chroma, chromagram(video_audio))
                    plan: AlignmentPlan | None = None
                    video_duration = video_audio.size / float(SR)
                    if _should_run_smart_analysis(ref_duration, video_duration):
                        self.log.emit(_worker_tr(self.language, "smart_analyzing", path=video_path))
                        plan = build_alignment_plan(ref_audio, video_audio, result, sr=SR)
                    self.result.emit(
                        index,
                        {
                            "path": video_path,
                            "ok": True,
                            **asdict(result),
                            **_plan_payload(plan, base_needs_review=result.confidence < 2.0),
                        },
                    )
                    self.log.emit(
                        _worker_tr(
                            self.language,
                            "detected_result",
                            offset=f"{result.offset_s:.3f}",
                            confidence=f"{result.confidence:.2f}",
                            path=video_path,
                        )
                    )
                    logger.info(
                        "Analysis complete for %s: offset=%.3f confidence=%.2f review=%s",
                        video_path,
                        result.offset_s,
                        result.confidence,
                        bool(plan.needs_review if plan is not None else result.confidence < 2.0),
                    )
                    record_metric("rhythmflow.analysis.video.completed", 1)
                    if plan is not None and plan.trimmed_segment_count > 0:
                        self.log.emit(
                            _worker_tr(
                                self.language,
                                "smart_result",
                                trim=f"{plan.trim_total_s:.2f}",
                                count=str(plan.trimmed_segment_count),
                                confidence=f"{plan.confidence:.2f}",
                                path=video_path,
                            )
                        )
                except Exception as exc:
                    logger.exception("Analysis failed for %s", video_path)
                    record_metric("rhythmflow.analysis.video.failed", 1)
                    self.result.emit(
                        index,
                        {
                            "path": video_path,
                            "ok": False,
                            "error": str(exc),
                        },
                    )
                    self.error.emit(f"{video_path}: {exc}")
                finally:
                    self.progress.emit(int((index + 1) / total * 100))
        except Exception as exc:
            logger.exception("Analysis worker failed")
            record_metric("rhythmflow.analysis.failed", 1)
            self.error.emit(str(exc))
        finally:
            logger.info("Analysis worker finished")
            self.finished.emit()


class ProcessWorker(QObject):
    progress = pyqtSignal(int)
    file_started = pyqtSignal(str)
    file_finished = pyqtSignal(str, str)
    log = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, jobs: list[ProcessJob], language: str = "zh") -> None:
        super().__init__()
        self.jobs = jobs
        self.language = language

    @pyqtSlot()
    def run(self) -> None:
        total = max(1, len(self.jobs))
        logger.info("Process worker started for %d job(s)", len(self.jobs))
        record_metric("rhythmflow.processing.started", 1)
        try:
            for index, job in enumerate(self.jobs):
                logger.info(
                    "Processing job %d/%d: video=%s output=%s",
                    index + 1,
                    total,
                    job.video_path,
                    job.output_path,
                )
                self.file_started.emit(job.video_path)

                def on_file_progress(value: int) -> None:
                    overall = int((index + value / 100) / total * 100)
                    self.progress.emit(overall)

                try:
                    output_path = process_video(job, on_progress=on_file_progress)
                    self.file_finished.emit(job.video_path, output_path)
                    self.log.emit(_worker_tr(self.language, "finished_path", path=output_path))
                    logger.info("Processing complete for %s: %s", job.video_path, output_path)
                    record_metric("rhythmflow.processing.video.completed", 1)
                except Exception as exc:
                    logger.exception("Processing failed for %s", job.video_path)
                    record_metric("rhythmflow.processing.video.failed", 1)
                    self.error.emit(f"{job.video_path}: {exc}")
                finally:
                    self.progress.emit(int((index + 1) / total * 100))
        finally:
            logger.info("Process worker finished")
            self.finished.emit()


def _worker_tr(language: str, key: str, **kwargs: object) -> str:
    worker_strings = {
        "en": {
            "decoding_reference": "Decoding reference audio...",
            "analyzing_path": "Analyzing {path}",
            "video_no_audio": "Video has no audio track, so it cannot be aligned automatically",
            "detected_result": "Detected {offset}s, confidence {confidence}: {path}",
            "smart_analyzing": "Running smart segment analysis: {path}",
            "smart_result": "Smart trim {trim}s in {count} segment(s), AI confidence {confidence}: {path}",
            "finished_path": "Finished {path}",
        },
        "zh": {
            "decoding_reference": "正在解码参考音频...",
            "analyzing_path": "正在分析 {path}",
            "video_no_audio": "视频没有音频轨道，无法自动对齐",
            "detected_result": "检测到 {offset}s，置信度 {confidence}：{path}",
            "smart_analyzing": "正在进行智能分段分析：{path}",
            "smart_result": "智能裁掉 {trim}s，共 {count} 段，AI 置信度 {confidence}：{path}",
            "finished_path": "已完成 {path}",
        },
    }
    values = worker_strings.get(language, worker_strings["zh"])
    return values.get(key, tr(language, key)).format(**kwargs)


def _should_run_smart_analysis(ref_duration: float, video_duration: float) -> bool:
    return abs(ref_duration - video_duration) >= MIN_USEFUL_TRIM_S


def _plan_payload(plan: AlignmentPlan | None, *, base_needs_review: bool = False) -> dict[str, object]:
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
