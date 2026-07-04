from __future__ import annotations

import unittest

from rhythmflow.core.pipeline import ProcessJob
from rhythmflow.webui.state import AppState, JobBuildError


def _ok_result(offset: float, confidence: float, **extra: object) -> dict[str, object]:
    data: dict[str, object] = {
        "ok": True,
        "offset_s": offset,
        "confidence": confidence,
        "smart_trim_s": 0.0,
        "smart_trim_count": 0,
        "smart_confidence": None,
        "needs_review": confidence < 2.0,
        "alignment_plan": None,
    }
    data.update(extra)
    return data


class StateRowTests(unittest.TestCase):
    def test_sync_rows_preserves_existing_analysis(self) -> None:
        state = AppState()
        state.sync_rows(["a.mp4", "b.mp4"])
        state.apply_analyze_result(0, _ok_result(1.0, 2.5))
        state.sync_rows(["a.mp4", "c.mp4"])
        self.assertTrue(state.rows[0].analyzed)
        self.assertEqual(state.rows[0].detected_offset, 1.0)
        self.assertFalse(state.rows[1].analyzed)

    def test_final_offset_adds_nudge(self) -> None:
        state = AppState()
        state.sync_rows(["a.mp4"])
        state.apply_analyze_result(0, _ok_result(2.0, 2.5))
        state.set_nudge(0, 0.25)
        self.assertAlmostEqual(state.rows[0].final_offset or 0.0, 2.25)

    def test_nudge_invalidates_review_confirmation(self) -> None:
        state = AppState()
        state.sync_rows(["a.mp4"])
        state.apply_analyze_result(0, _ok_result(3.0, 1.0))  # needs review
        state.rows[0].review_confirmed = True
        state.set_nudge(0, 0.1)
        self.assertFalse(state.rows[0].review_confirmed)

    def test_suspicious_offset_forces_review(self) -> None:
        state = AppState()
        state.sync_rows(["a.mp4"])
        # High confidence but a huge global offset should still require review.
        state.apply_analyze_result(0, _ok_result(20.0, 5.0, needs_review=False))
        self.assertTrue(state.rows[0].needs_review)
        self.assertIn(0, state.unconfirmed_review_rows())

    def test_error_result_clears_row(self) -> None:
        state = AppState()
        state.sync_rows(["a.mp4"])
        state.apply_analyze_result(0, {"ok": False, "error": "boom"})
        self.assertFalse(state.rows[0].analyzed)
        self.assertEqual(state.rows[0].error, "boom")
        self.assertIsNone(state.rows[0].detected_offset)


class ReviewSegmentTests(unittest.TestCase):
    def _analyzed_state(self, offset: float, confidence: float) -> AppState:
        state = AppState()
        state.update_context({"reference_path": "ref.wav"})
        state.sync_rows(["clip.mp4"])
        state.apply_analyze_result(0, _ok_result(offset, confidence))
        return state

    def test_global_segment(self) -> None:
        state = self._analyzed_state(1.5, 1.0)
        segments = state.review_segments()
        self.assertEqual(len(segments), 1)
        seg = segments[0]
        self.assertTrue(seg["is_global"])
        self.assertEqual(seg["label_key"], "review_global_label")
        self.assertAlmostEqual(seg["video_start_s"], 1.5)
        self.assertAlmostEqual(seg["reference_start_s"], 0.0)

    def test_large_offset_segment_has_warning_note(self) -> None:
        state = self._analyzed_state(20.0, 5.0)
        segments = state.review_segments()
        seg = segments[0]
        self.assertEqual(seg["label_key"], "review_large_offset_label")
        note_keys = [note["key"] for note in seg["notes"]]
        self.assertIn("review_large_offset_note", note_keys)

    def test_apply_review_confirms_and_shifts_global_nudge(self) -> None:
        state = self._analyzed_state(1.5, 1.0)
        state.apply_review([{"row": 0, "segment_index": 0, "delta_s": 0.4}])
        self.assertTrue(state.rows[0].review_confirmed)
        self.assertAlmostEqual(state.rows[0].nudge, 0.4)
        self.assertEqual(state.unconfirmed_review_rows(), [])

    def test_segmented_plan_produces_per_segment_reviews(self) -> None:
        plan = {
            "method": "segmented",
            "video_start_s": 0.0,
            "duration_s": 8.0,
            "trim_total_s": 2.0,
            "reference_segments": [
                {"start_s": 0.0, "end_s": 4.0},
                {"start_s": 6.0, "end_s": 10.0},
            ],
            "video_segments": [
                {"start_s": 0.0, "end_s": 4.0},
                {"start_s": 4.0, "end_s": 8.0},
            ],
            "warnings": ["check the middle cut"],
        }
        state = AppState()
        state.update_context({"reference_path": "ref.wav"})
        state.sync_rows(["clip.mp4"])
        state.apply_analyze_result(
            0, _ok_result(0.0, 1.5, alignment_plan=plan, smart_trim_s=2.0, smart_trim_count=1)
        )
        segments = state.review_segments()
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0]["label_key"], "review_segment_label")
        self.assertEqual(segments[0]["label_params"], {"index": 1})
        self.assertFalse(segments[0]["is_global"])


class JobBuildTests(unittest.TestCase):
    def test_missing_reference_raises(self) -> None:
        state = AppState()
        state.update_context({"output_dir": "out"})
        state.sync_rows(["clip.mp4"])
        state.apply_analyze_result(0, _ok_result(1.0, 2.5))
        with self.assertRaises(JobBuildError) as ctx:
            state.build_jobs()
        self.assertEqual(ctx.exception.key, "warn_choose_reference")

    def test_unconfirmed_review_blocks_jobs(self) -> None:
        state = AppState()
        state.update_context({"reference_path": "ref.wav", "output_dir": "out"})
        state.sync_rows(["clip.mp4"])
        state.apply_analyze_result(0, _ok_result(1.0, 1.0))  # needs review
        with self.assertRaises(JobBuildError) as ctx:
            state.build_jobs()
        self.assertEqual(ctx.exception.key, "warn_review_required")

    def test_build_jobs_uses_final_offset_and_volumes(self) -> None:
        state = AppState()
        state.update_context(
            {
                "reference_path": "ref.wav",
                "output_dir": "out",
                "output_pattern": "{name}_aligned.mp4",
                "original_volume": 20,
                "reference_volume": 80,
                "mode": "fast",
            }
        )
        state.sync_rows(["clip.mp4"])
        state.apply_analyze_result(0, _ok_result(2.0, 2.5))
        state.set_nudge(0, 0.5)
        jobs = state.build_jobs()
        self.assertEqual(len(jobs), 1)
        job = jobs[0]
        self.assertIsInstance(job, ProcessJob)
        self.assertAlmostEqual(job.offset_s, 2.5)
        self.assertAlmostEqual(job.original_volume, 0.2)
        self.assertAlmostEqual(job.reference_volume, 0.8)
        self.assertEqual(job.mode, "fast")
        self.assertTrue(job.output_path.endswith("clip_aligned.mp4"))


if __name__ == "__main__":
    unittest.main()
