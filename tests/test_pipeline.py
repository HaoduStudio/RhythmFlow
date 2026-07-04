from __future__ import annotations

import unittest

from rhythmflow.core.pipeline import CutWindow, ProcessJob, build_ffmpeg_args, build_output_path, compute_cut_window
from rhythmflow.core.segmented_alignment import ReferenceSegment


class PipelineTests(unittest.TestCase):
    def test_cut_window_positive_offset(self) -> None:
        window = compute_cut_window(3.0, ref_duration_s=90.0, video_duration_s=120.0)
        self.assertEqual(window, CutWindow(video_start=3.0, reference_start=0.0, duration=90.0))

    def test_cut_window_negative_offset(self) -> None:
        window = compute_cut_window(-2.0, ref_duration_s=90.0, video_duration_s=120.0)
        self.assertEqual(window, CutWindow(video_start=0.0, reference_start=2.0, duration=88.0))

    def test_fast_args_copy_video_and_keep_amix_normalize_zero(self) -> None:
        job = ProcessJob(
            video_path="input.mp4",
            reference_audio_path="ref.wav",
            output_path="out.mp4",
            offset_s=1.0,
            original_volume=0.15,
            reference_volume=1.0,
            mode="fast",
        )
        args = build_ffmpeg_args(job, CutWindow(1.0, 0.0, 10.0))
        joined = " ".join(args)
        self.assertIn("-c:v copy", joined)
        self.assertIn("normalize=0", joined)

    def test_segmented_reference_args_concat_trimmed_audio(self) -> None:
        job = ProcessJob(
            video_path="input.mp4",
            reference_audio_path="ref.wav",
            output_path="out.mp4",
            offset_s=0.0,
            original_volume=0.15,
            reference_volume=1.0,
            mode="accurate",
            video_start_s=1.25,
            duration_s=4.0,
            reference_segments=(
                ReferenceSegment(0.0, 2.0),
                ReferenceSegment(3.5, 5.5),
            ),
        )

        args = build_ffmpeg_args(job, CutWindow(1.25, 0.0, 4.0))
        joined = " ".join(args)

        self.assertIn("-ss 1.250000 -i input.mp4 -i ref.wav", joined)
        self.assertIn("atrim=start=0.000000:end=2.000000", joined)
        self.assertIn("atrim=start=3.500000:end=5.500000", joined)
        self.assertIn("concat=n=2:v=0:a=1[refcat]", joined)

    def test_segmented_video_args_concat_trimmed_video_and_audio(self) -> None:
        job = ProcessJob(
            video_path="input.mp4",
            reference_audio_path="ref.wav",
            output_path="out.mp4",
            offset_s=0.0,
            original_volume=0.15,
            reference_volume=1.0,
            mode="fast",
            video_segments=(
                ReferenceSegment(0.0, 2.0),
                ReferenceSegment(5.0, 7.0),
            ),
            reference_segments=(
                ReferenceSegment(0.0, 2.0),
                ReferenceSegment(2.0, 4.0),
            ),
        )

        args = build_ffmpeg_args(job, CutWindow(0.0, 0.0, 4.0))
        joined = " ".join(args)

        self.assertIn("[0:v]trim=start=0.000000:end=2.000000", joined)
        self.assertIn("[0:v]trim=start=5.000000:end=7.000000", joined)
        self.assertIn("[0:a]atrim=start=5.000000:end=7.000000", joined)
        self.assertIn("[1:a]atrim=start=2.000000:end=4.000000", joined)
        self.assertIn("concat=n=2:v=1:a=0[vcat]", joined)
        self.assertIn("concat=n=2:v=0:a=1[oacat]", joined)
        self.assertIn("-map [vout]", joined)
        self.assertIn("-preset veryfast", joined)
        self.assertNotIn("-c:v copy", joined)

    def test_segmented_video_args_can_keep_only_original_audio(self) -> None:
        job = ProcessJob(
            video_path="input.mp4",
            reference_audio_path="ref.wav",
            output_path="out.mp4",
            offset_s=0.0,
            original_volume=1.0,
            reference_volume=0.0,
            mode="accurate",
            video_segments=(
                ReferenceSegment(0.0, 2.0),
                ReferenceSegment(5.0, 7.0),
            ),
            reference_segments=(
                ReferenceSegment(0.0, 2.0),
                ReferenceSegment(2.0, 4.0),
            ),
        )

        args = build_ffmpeg_args(job, CutWindow(0.0, 0.0, 4.0))
        joined = " ".join(args)

        self.assertIn("concat=n=2:v=0:a=1[oacat]", joined)
        self.assertIn("[oacat]volume=1.0000[aout]", joined)
        self.assertNotIn("[1:a]atrim", joined)
        self.assertNotIn("[refcat]", joined)

    def test_output_pattern(self) -> None:
        path = build_output_path(r"C:\clips\Hand Cam 01.mov", r"C:\out", "{index}_{name}", 3)
        self.assertTrue(path.endswith(r"C:\out\3_Hand Cam 01.mp4"))


if __name__ == "__main__":
    unittest.main()
