from __future__ import annotations

import unittest

from rhythmflow.ui.review_dialog import (
    ReviewSegment,
    _adjusted_spans,
    _adjustment_limit_for_segment,
)


class ReviewDialogAlignmentTests(unittest.TestCase):
    def test_large_positive_global_offset_can_adjust_back_to_zero(self) -> None:
        segment = ReviewSegment(
            row=0,
            file_name="handcam.mp4",
            video_path="handcam.mp4",
            reference_path="ref.wav",
            label="Large offset preview",
            reference_start_s=0.0,
            reference_end_s=8.0,
            video_start_s=50.66,
            video_end_s=58.66,
            is_global=True,
        )

        limit = _adjustment_limit_for_segment(segment)
        ref_start, ref_end, video_start, video_end = _adjusted_spans(segment, -50.66)

        self.assertGreaterEqual(limit, 50.66)
        self.assertEqual((ref_start, video_start), (0.0, 0.0))
        self.assertAlmostEqual(ref_end, 8.0)
        self.assertAlmostEqual(video_end, 8.0)

    def test_large_negative_global_offset_recomputes_reference_span(self) -> None:
        segment = ReviewSegment(
            row=0,
            file_name="handcam.mp4",
            video_path="handcam.mp4",
            reference_path="ref.wav",
            label="Large offset preview",
            reference_start_s=50.0,
            reference_end_s=58.0,
            video_start_s=0.0,
            video_end_s=8.0,
            is_global=True,
        )

        ref_start, ref_end, video_start, video_end = _adjusted_spans(segment, 50.0)

        self.assertEqual((ref_start, video_start), (0.0, 0.0))
        self.assertAlmostEqual(ref_end, 8.0)
        self.assertAlmostEqual(video_end, 8.0)


if __name__ == "__main__":
    unittest.main()
