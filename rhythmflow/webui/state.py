from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rhythmflow.config import DEFAULT_LANGUAGE, DEFAULT_OUTPUT_PATTERN
from rhythmflow.core.pipeline import ProcessJob, build_output_path
from rhythmflow.core.segmented_alignment import ReferenceSegment

logger = logging.getLogger(__name__)

SUSPICIOUS_GLOBAL_OFFSET_S = 15.0
PREVIEW_DURATION_S = 8.0


@dataclass
class RowState:
    video_path: str
    analyzed: bool = False
    error: str | None = None
    detected_offset: float | None = None
    confidence: float | None = None
    nudge: float = 0.0
    smart_trim_s: float = 0.0
    smart_trim_count: int = 0
    smart_confidence: float | None = None
    needs_review: bool = False
    review_confirmed: bool = False
    alignment_plan: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def file_name(self) -> str:
        return Path(self.video_path).name

    @property
    def final_offset(self) -> float | None:
        if self.detected_offset is None:
            return None
        return round(self.detected_offset + self.nudge, 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "file_name": self.file_name,
            "analyzed": self.analyzed,
            "error": self.error,
            "detected_offset": self.detected_offset,
            "confidence": self.confidence,
            "nudge": self.nudge,
            "final_offset": self.final_offset,
            "smart_trim_s": self.smart_trim_s,
            "smart_trim_count": self.smart_trim_count,
            "smart_confidence": self.smart_confidence,
            "needs_review": self.needs_review,
            "review_confirmed": self.review_confirmed,
            "warnings": list(self.warnings),
        }


class AppState:
    def __init__(self) -> None:
        self.rows: list[RowState] = []
        self.language = DEFAULT_LANGUAGE
        self.reference_path = ""
        self.output_dir = ""
        self.output_pattern = DEFAULT_OUTPUT_PATTERN
        self.original_volume = 15
        self.reference_volume = 100
        self.mode = "accurate"

    def update_context(self, context: dict[str, Any] | None) -> None:
        if not context:
            return
        if "language" in context:
            self.language = str(context["language"] or DEFAULT_LANGUAGE)
        if "reference_path" in context:
            self.reference_path = str(context["reference_path"] or "")
        if "output_dir" in context:
            self.output_dir = str(context["output_dir"] or "")
        if "output_pattern" in context:
            self.output_pattern = str(context["output_pattern"] or "") or DEFAULT_OUTPUT_PATTERN
        if "original_volume" in context:
            self.original_volume = int(context["original_volume"])
        if "reference_volume" in context:
            self.reference_volume = int(context["reference_volume"])
        if "mode" in context:
            self.mode = str(context["mode"] or "accurate")

    def sync_rows(self, paths: list[str]) -> list[dict[str, Any]]:
        normalized = [str(Path(path)) for path in paths]
        existing = {row.video_path: row for row in self.rows}
        self.rows = [existing.get(path) or RowState(video_path=path) for path in normalized]
        return self.rows_payload()

    def rows_payload(self) -> list[dict[str, Any]]:
        return [row.to_dict() for row in self.rows]

    def set_nudge(self, row_index: int, value: float) -> dict[str, Any] | None:
        if not 0 <= row_index < len(self.rows):
            return None
        row = self.rows[row_index]
        row.nudge = float(value)
        if row.needs_review:
            row.review_confirmed = False
        return row.to_dict()

    def apply_analyze_result(self, row_index: int, data: dict[str, Any]) -> dict[str, Any] | None:
        if not 0 <= row_index < len(self.rows):
            logger.warning("Ignoring analysis result for missing row %d", row_index)
            return None
        row = self.rows[row_index]
        if not data.get("ok"):
            row.analyzed = False
            row.error = str(data.get("error") or "error")
            row.detected_offset = None
            row.confidence = None
            row.smart_trim_s = 0.0
            row.smart_trim_count = 0
            row.smart_confidence = None
            row.needs_review = False
            row.review_confirmed = False
            row.alignment_plan = None
            row.warnings = []
            return row.to_dict()

        offset = float(data["offset_s"])
        confidence = float(data["confidence"])
        plan = data.get("alignment_plan")
        needs_review = bool(data.get("needs_review"))
        if abs(offset) >= SUSPICIOUS_GLOBAL_OFFSET_S and not (
            isinstance(plan, dict) and plan.get("method") == "segmented"
        ):
            needs_review = True

        row.analyzed = True
        row.error = None
        row.detected_offset = offset
        row.confidence = confidence
        row.smart_trim_s = float(data.get("smart_trim_s") or 0.0)
        row.smart_trim_count = int(data.get("smart_trim_count") or 0)
        smart_confidence = data.get("smart_confidence")
        row.smart_confidence = None if smart_confidence is None else float(smart_confidence)
        row.needs_review = needs_review
        row.review_confirmed = not needs_review
        row.alignment_plan = plan if isinstance(plan, dict) else None
        row.warnings = _plan_warnings(plan)
        return row.to_dict()

    def unconfirmed_review_rows(self) -> list[int]:
        return [
            index
            for index, row in enumerate(self.rows)
            if row.needs_review and not row.review_confirmed
        ]

    def review_segments(self) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for row_index in self.unconfirmed_review_rows():
            segments.extend(self._review_segments_for_row(row_index))
        return segments

    def _review_segments_for_row(self, row_index: int) -> list[dict[str, Any]]:
        row = self.rows[row_index]
        reference = self.reference_path
        final_offset = row.final_offset
        if final_offset is None or not row.video_path or not reference:
            return []

        plan = row.alignment_plan
        note = self._review_note(row)
        if isinstance(plan, dict) and plan.get("method") == "segmented":
            reference_segments = _parse_plan_segments(plan.get("reference_segments"))
            video_segments = _parse_plan_segments(plan.get("video_segments"))
            if reference_segments:
                result: list[dict[str, Any]] = []
                nudge = row.nudge
                video_cursor = max(0.0, float(plan.get("video_start_s") or 0.0) + nudge)
                has_paired = len(video_segments) == len(reference_segments)
                for index, (ref_start, ref_end) in enumerate(reference_segments, start=1):
                    duration = max(0.0, ref_end - ref_start)
                    if duration <= 0.0:
                        continue
                    if has_paired:
                        v_start, v_end = video_segments[index - 1]
                        video_start = max(0.0, v_start + nudge)
                        video_end = max(video_start, v_end + nudge)
                    else:
                        video_start = video_cursor
                        video_end = video_cursor + duration
                    result.append(
                        self._make_segment(
                            row_index,
                            row,
                            reference,
                            label_key="review_segment_label",
                            label_params={"index": index},
                            reference_start_s=ref_start,
                            reference_end_s=ref_end,
                            video_start_s=video_start,
                            video_end_s=video_end,
                            notes=[note],
                            segment_index=index - 1,
                            is_global=False,
                        )
                    )
                    video_cursor = video_end
                if result:
                    return result

        offset = final_offset
        video_start = max(0.0, offset)
        reference_start = max(0.0, -offset)
        notes = [note]
        if abs(offset) >= SUSPICIOUS_GLOBAL_OFFSET_S:
            label_key = "review_large_offset_label"
            notes.append(
                {
                    "key": "review_large_offset_note",
                    "params": {
                        "offset": f"{offset:.2f}",
                        "threshold": f"{SUSPICIOUS_GLOBAL_OFFSET_S:.0f}",
                    },
                }
            )
        else:
            label_key = "review_global_label"
        return [
            self._make_segment(
                row_index,
                row,
                reference,
                label_key=label_key,
                label_params={},
                reference_start_s=reference_start,
                reference_end_s=reference_start + PREVIEW_DURATION_S,
                video_start_s=video_start,
                video_end_s=video_start + PREVIEW_DURATION_S,
                notes=notes,
                segment_index=0,
                is_global=True,
            )
        ]

    def _make_segment(
        self,
        row_index: int,
        row: RowState,
        reference: str,
        *,
        label_key: str,
        label_params: dict[str, Any],
        reference_start_s: float,
        reference_end_s: float,
        video_start_s: float,
        video_end_s: float,
        notes: list[dict[str, Any]],
        segment_index: int,
        is_global: bool,
    ) -> dict[str, Any]:
        return {
            "id": f"{row_index}:{segment_index}",
            "row": row_index,
            "segment_index": segment_index,
            "is_global": is_global,
            "file_name": row.file_name,
            "video_path": row.video_path,
            "reference_path": reference,
            "label_key": label_key,
            "label_params": label_params,
            "notes": notes,
            "reference_start_s": reference_start_s,
            "reference_end_s": reference_end_s,
            "video_start_s": video_start_s,
            "video_end_s": video_end_s,
        }

    def _review_note(self, row: RowState) -> dict[str, Any]:
        if row.warnings:
            return {"key": "smart_warnings", "params": {"warnings": ", ".join(row.warnings)}}
        confidence = "-" if row.confidence is None else f"{row.confidence:.2f}"
        return {"key": "review_low_confidence_note", "params": {"confidence": confidence}}

    def apply_review(self, deltas: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        delta_map: dict[tuple[int, int], float] = {}
        confirmed_rows: set[int] = set()
        for entry in deltas or []:
            row_index = int(entry["row"])
            segment_index = int(entry["segment_index"])
            delta_map[(row_index, segment_index)] = float(entry.get("delta_s", 0.0))
            confirmed_rows.add(row_index)

        grouped: dict[int, list[dict[str, Any]]] = {}
        for segment in self.review_segments():
            row_index = int(segment["row"])
            confirmed_rows.add(row_index)
            delta = delta_map.get((row_index, int(segment["segment_index"])), 0.0)
            ref_start, ref_end, video_start, video_end = _adjusted_spans(segment, delta)
            grouped.setdefault(row_index, []).append(
                {
                    "segment_index": int(segment["segment_index"]),
                    "reference_start_s": ref_start,
                    "reference_end_s": ref_end,
                    "video_start_s": video_start,
                    "video_end_s": video_end,
                    "delta_s": delta,
                }
            )

        for row_index, adjustments in grouped.items():
            self._apply_row_adjustments(row_index, adjustments)

        for row_index in confirmed_rows:
            if 0 <= row_index < len(self.rows):
                self.rows[row_index].review_confirmed = True

        return self.rows_payload()

    def _apply_row_adjustments(self, row_index: int, adjustments: list[dict[str, Any]]) -> None:
        if not adjustments or not 0 <= row_index < len(self.rows):
            return
        row = self.rows[row_index]
        plan = row.alignment_plan
        if isinstance(plan, dict) and plan.get("method") == "segmented":
            row.alignment_plan = _plan_with_review_adjustments(row.nudge, plan, adjustments)
            return
        delta_s = adjustments[0]["delta_s"]
        if abs(delta_s) >= 0.0005:
            row.nudge = row.nudge + delta_s

    def build_jobs(self) -> list[ProcessJob]:
        reference = self.reference_path
        output_dir = self.output_dir
        if not reference:
            raise JobBuildError("warn_choose_reference")
        if not output_dir:
            raise JobBuildError("warn_choose_output")
        if self.unconfirmed_review_rows():
            raise JobBuildError("warn_review_required")
        if not self.rows:
            raise JobBuildError("warn_add_analyze")

        jobs: list[ProcessJob] = []
        for row_index, row in enumerate(self.rows):
            if not row.analyzed or row.detected_offset is None or row.error:
                raise JobBuildError("warn_analyze_all")
            offset = row.final_offset or 0.0
            video_start_s, duration_s, video_segments, reference_segments = _job_plan_values(
                row.alignment_plan, row.nudge
            )
            output_path = build_output_path(
                row.video_path, output_dir, self.output_pattern, row_index + 1
            )
            jobs.append(
                ProcessJob(
                    video_path=row.video_path,
                    reference_audio_path=reference,
                    output_path=output_path,
                    offset_s=offset,
                    original_volume=self.original_volume / 100,
                    reference_volume=self.reference_volume / 100,
                    mode=self.mode,
                    video_start_s=video_start_s,
                    duration_s=duration_s,
                    video_segments=video_segments,
                    reference_segments=reference_segments,
                )
            )
        return jobs


class JobBuildError(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _plan_warnings(plan: object) -> list[str]:
    if isinstance(plan, dict):
        warnings = plan.get("warnings")
        if isinstance(warnings, list):
            return [str(item) for item in warnings]
    return []


def _parse_plan_segments(value: object) -> list[tuple[float, float]]:
    if not isinstance(value, list):
        return []
    segments: list[tuple[float, float]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        start = float(raw.get("start_s", 0.0))
        end = float(raw.get("end_s", 0.0))
        if end - start > 0.0:
            segments.append((start, end))
    return segments


def _adjusted_spans(segment: dict[str, Any], adjustment_s: float) -> tuple[float, float, float, float]:
    duration_s = max(
        0.1,
        min(
            max(0.0, float(segment["reference_end_s"]) - float(segment["reference_start_s"])),
            max(0.0, float(segment["video_end_s"]) - float(segment["video_start_s"])),
        ),
    )
    if segment.get("is_global"):
        offset_s = (float(segment["video_start_s"]) - float(segment["reference_start_s"])) + adjustment_s
        reference_start_s = max(0.0, -offset_s)
        video_start_s = max(0.0, offset_s)
        return (
            reference_start_s,
            reference_start_s + duration_s,
            video_start_s,
            video_start_s + duration_s,
        )
    video_start_s = max(0.0, float(segment["video_start_s"]) + adjustment_s)
    video_end_s = max(video_start_s, float(segment["video_end_s"]) + adjustment_s)
    return (
        float(segment["reference_start_s"]),
        float(segment["reference_end_s"]),
        video_start_s,
        video_end_s,
    )


def _plan_with_review_adjustments(
    nudge: float,
    plan: dict[str, Any],
    adjustments: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered = sorted(adjustments, key=lambda item: item["segment_index"])
    video_segments = [
        {
            "start_s": max(0.0, item["video_start_s"] - nudge),
            "end_s": max(0.0, item["video_end_s"] - nudge),
        }
        for item in ordered
    ]
    reference_segments = [
        {"start_s": item["reference_start_s"], "end_s": item["reference_end_s"]}
        for item in ordered
    ]
    duration_s = sum(
        min(
            max(0.0, video["end_s"] - video["start_s"]),
            max(0.0, reference["end_s"] - reference["start_s"]),
        )
        for video, reference in zip(video_segments, reference_segments)
    )
    updated = dict(plan)
    updated["video_segments"] = video_segments
    updated["reference_segments"] = reference_segments
    updated["duration_s"] = duration_s
    updated["review_adjusted"] = True
    if video_segments:
        updated["video_start_s"] = video_segments[0]["start_s"]
        updated["video_end_s"] = video_segments[-1]["end_s"]
    return updated


def _job_plan_values(
    plan: object,
    nudge: float,
) -> tuple[float | None, float | None, tuple[ReferenceSegment, ...], tuple[ReferenceSegment, ...]]:
    if not isinstance(plan, dict) or plan.get("method") != "segmented":
        return None, None, (), ()
    if float(plan.get("trim_total_s") or 0.0) <= 0.0:
        return None, None, (), ()
    reference_segments = [ReferenceSegment(s, e) for s, e in _parse_plan_segments(plan.get("reference_segments"))]
    video_segments = [ReferenceSegment(s, e) for s, e in _parse_plan_segments(plan.get("video_segments"))]
    if not reference_segments:
        return None, None, (), ()

    if len(video_segments) == len(reference_segments):
        shifted_video = tuple(
            ReferenceSegment(max(0.0, seg.start_s + nudge), max(0.0, seg.end_s + nudge))
            for seg in video_segments
        )
        duration_s = sum(
            min(video_seg.duration_s, reference_seg.duration_s)
            for video_seg, reference_seg in zip(shifted_video, reference_segments)
        )
        return shifted_video[0].start_s, duration_s, shifted_video, tuple(reference_segments)

    video_start_s = max(0.0, float(plan.get("video_start_s") or 0.0) + nudge)
    duration_s = max(0.0, float(plan.get("duration_s") or 0.0))
    return video_start_s, duration_s, (), tuple(reference_segments)
