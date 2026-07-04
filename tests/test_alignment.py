from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.io import wavfile

from rhythmflow.config import SR
from rhythmflow.core.alignment import AlignmentResult, AudioAligner, TapFeatures, chromagram, find_offset
from rhythmflow.core.segmented_alignment import build_alignment_plan


def _melody(seconds_per_note: float = 0.22) -> np.ndarray:
    notes = [261.63, 329.63, 392.00, 293.66, 440.00, 349.23, 493.88, 311.13, 415.30, 370.00]
    chunks = []
    for index, freq in enumerate(notes):
        length = int(seconds_per_note * SR)
        t = np.arange(length, dtype=np.float32) / SR
        tone = 0.6 * np.sin(2 * np.pi * freq * t)
        tone += 0.25 * np.sin(2 * np.pi * freq * 1.5 * t)
        fade = min(256, max(1, length // 8))
        envelope = np.ones(length, dtype=np.float32)
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        envelope[:fade] = ramp
        envelope[-fade:] = ramp[::-1]
        chunks.append((tone * envelope).astype(np.float32))
    return np.concatenate(chunks)


def _reference_signal(sr: int, duration_s: float = 2.5) -> np.ndarray:
    t = np.arange(int(duration_s * sr), dtype=np.float32) / sr
    tone = 0.35 * np.sin(2 * np.pi * 440.0 * t)
    tone += 0.25 * np.sin(2 * np.pi * 659.25 * t)
    tone += 0.15 * np.sin(2 * np.pi * 987.77 * t)
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * 2.7 * t) ** 2
    return (tone * envelope).astype(np.float32)


def _phrase(freqs: list[float], seconds_per_note: float = 0.22) -> np.ndarray:
    chunks = []
    for index, freq in enumerate(freqs):
        length = int(seconds_per_note * SR)
        t = np.arange(length, dtype=np.float32) / SR
        tone = 0.45 * np.sin(2 * np.pi * freq * t)
        tone += 0.22 * np.sin(2 * np.pi * freq * 2.01 * t)
        tone += 0.12 * np.sin(2 * np.pi * (freq * 0.5 + 35.0) * t)
        pulse = 0.65 + 0.35 * np.sin(2 * np.pi * (3.0 + index * 0.17) * t) ** 2
        fade = min(320, max(1, length // 6))
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        envelope = np.ones(length, dtype=np.float32)
        envelope[:fade] = ramp
        envelope[-fade:] = ramp[::-1]
        chunks.append((tone * pulse * envelope).astype(np.float32))
    return np.concatenate(chunks)


def _write_wav(path: Path, sr: int, audio: np.ndarray) -> None:
    wavfile.write(path, sr, np.asarray(audio, dtype=np.float32))


class AlignmentTests(unittest.TestCase):
    def test_positive_offset(self) -> None:
        ref = _melody()
        known = 0.55
        prefix = np.zeros(int(known * SR), dtype=np.float32)
        tail = np.zeros(int(0.2 * SR), dtype=np.float32)
        video = np.concatenate([prefix, ref, tail])

        result = find_offset(chromagram(ref), chromagram(video))

        self.assertAlmostEqual(result.offset_s, known, delta=0.08)
        self.assertGreater(result.confidence, 3.0)

    def test_negative_offset(self) -> None:
        ref = _melody()
        known = -0.44
        video = ref[int(abs(known) * SR) :]

        result = find_offset(chromagram(ref), chromagram(video))

        self.assertAlmostEqual(result.offset_s, known, delta=0.08)
        self.assertGreater(result.confidence, 2.0)

    def test_calculate_offset_resamples_mixed_sample_rates(self) -> None:
        aligner = AudioAligner(correlation_sr=22050)
        known = 0.23

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ref_sr = 44100
            video_sr = 48000
            reference = _reference_signal(ref_sr)
            video_body = _reference_signal(video_sr)
            video = np.concatenate(
                [
                    np.zeros(int(known * video_sr), dtype=np.float32),
                    video_body,
                    np.zeros(int(0.15 * video_sr), dtype=np.float32),
                ]
            )

            ref_path = root / "reference.wav"
            video_path = root / "game_bgm.wav"
            _write_wav(ref_path, ref_sr, reference)
            _write_wav(video_path, video_sr, video)

            result = aligner.calculate_offset(video_path, ref_path)

        self.assertAlmostEqual(result.offset_s, known, delta=0.02)
        self.assertEqual(result.sample_rate, 22050)

    def test_fine_tune_with_features_matches_chart_in_milliseconds(self) -> None:
        aligner = AudioAligner(
            fine_tune_window_ms=50.0,
            fine_tune_step_ms=1.0,
            match_tolerance_ms=10.0,
        )
        chart = np.array([0.25, 0.50, 0.75, 1.00, 1.25, 1.50], dtype=np.float32)
        true_offset = 1.037
        tap_times = chart + true_offset + np.array([0.000, 0.001, -0.001, 0.0, 0.001, 0.0])
        tap_features = TapFeatures(
            timestamps=tap_times,
            strengths=np.ones(tap_times.shape, dtype=np.float32),
            onset_envelope=np.ones(32, dtype=np.float32),
            sr=44100,
            hop_length=128,
        )

        result = aligner.fine_tune_with_features(1.02, tap_features, chart)

        self.assertAlmostEqual(result.offset_s, true_offset, delta=0.002)
        self.assertGreaterEqual(result.matched_taps, chart.size - 1)

    def test_extract_tap_features_detects_synthetic_clicks(self) -> None:
        aligner = AudioAligner(target_sr=44100, tap_hop_length=128)
        sr = 44100
        click_times = np.array([0.30, 0.75, 1.20], dtype=np.float32)
        audio = np.zeros(int(1.6 * sr), dtype=np.float32)
        decay = np.exp(-np.linspace(0.0, 6.0, 320)).astype(np.float32)
        carrier = np.sin(2 * np.pi * 4200.0 * np.arange(decay.size, dtype=np.float32) / sr)
        click = 0.9 * decay * carrier
        for item in click_times:
            start = int(item * sr)
            audio[start : start + click.size] += click

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tap_clicks.wav"
            _write_wav(path, sr, audio)
            features = aligner.extract_tap_features(path)

        for expected in click_times:
            nearest = np.min(np.abs(features.timestamps - expected))
            self.assertLess(nearest, 0.03)

    def test_segmented_alignment_trims_reference_middle_insert(self) -> None:
        part_a = _phrase([220, 277, 330, 392, 466, 523, 587, 659], 0.24)
        part_b = _phrase([698, 622, 554, 494, 440, 392, 349, 330], 0.24)
        extra_vocal = _phrase([185, 196, 208, 233, 247, 262], 0.22)
        reference = np.concatenate([part_a, extra_vocal, part_b])
        video = np.concatenate([part_a, part_b])
        noise = 0.012 * np.random.default_rng(42).standard_normal(video.size).astype(np.float32)
        video = np.clip(video + noise, -1.0, 1.0)

        plan = build_alignment_plan(reference, video, AlignmentResult(0.0, 1.0, 0), sr=SR)

        self.assertEqual(plan.method, "segmented")
        self.assertGreaterEqual(len(plan.reference_segments), 2)
        self.assertGreaterEqual(plan.trimmed_segment_count, 1)
        self.assertAlmostEqual(plan.trim_total_s, extra_vocal.size / SR, delta=0.55)
        self.assertGreater(plan.confidence, 0.5)
        self.assertFalse(plan.needs_review)

    def test_segmented_alignment_trims_video_middle_insert(self) -> None:
        part_a = _phrase([220, 277, 330, 392, 466, 523, 587, 659], 0.24)
        part_b = _phrase([698, 622, 554, 494, 440, 392, 349, 330], 0.24)
        extra_handcam = _phrase([185, 196, 208, 233, 247, 262], 0.22)
        reference = np.concatenate([part_a, part_b])
        video = np.concatenate([part_a, extra_handcam, part_b])
        noise = 0.012 * np.random.default_rng(43).standard_normal(video.size).astype(np.float32)
        video = np.clip(video + noise, -1.0, 1.0)

        plan = build_alignment_plan(reference, video, AlignmentResult(0.0, 1.0, 0), sr=SR)

        self.assertEqual(plan.method, "segmented")
        self.assertGreaterEqual(len(plan.video_segments), 2)
        self.assertGreaterEqual(plan.trimmed_segment_count, 1)
        self.assertAlmostEqual(plan.video_trim_total_s, extra_handcam.size / SR, delta=0.65)
        self.assertLess(plan.reference_trim_total_s, 0.5)
        self.assertGreater(plan.confidence, 0.5)
        self.assertFalse(plan.needs_review)

    def test_segmented_alignment_trims_multiple_video_only_sections(self) -> None:
        part_a = _phrase([220, 277, 330, 392, 466, 523], 0.24)
        extra_b = _phrase([185, 196, 208, 233], 0.24)
        part_c = _phrase([698, 622, 554, 494, 440, 392], 0.24)
        extra_d = _phrase([262, 294, 330, 370], 0.24)
        part_e = _phrase([349, 392, 440, 494, 523, 587], 0.24)
        reference = np.concatenate([part_a, part_c, part_e])
        video = np.concatenate([part_a, extra_b, part_c, extra_d, part_e])
        noise = 0.006 * np.random.default_rng(123).standard_normal(video.size).astype(np.float32)
        video = np.clip(video + noise, -1.0, 1.0)

        plan = build_alignment_plan(reference, video, AlignmentResult(0.0, 4.0, 0), sr=SR)

        self.assertEqual(plan.method, "segmented")
        self.assertEqual(len(plan.video_segments), 3)
        self.assertEqual(len(plan.reference_segments), 3)
        self.assertGreaterEqual(plan.trimmed_segment_count, 2)
        self.assertAlmostEqual(plan.video_trim_total_s, (extra_b.size + extra_d.size) / SR, delta=0.8)
        self.assertLess(plan.reference_trim_total_s, 0.35)
        self.assertGreater(plan.confidence, 0.65)
        self.assertFalse(plan.needs_review)

    def test_segmented_alignment_marks_too_short_audio_for_review(self) -> None:
        reference = _phrase([330, 392, 440], 0.12)
        video = reference.copy()

        plan = build_alignment_plan(reference, video, AlignmentResult(0.0, 6.0, 0), sr=SR)

        self.assertEqual(plan.method, "global")
        self.assertTrue(plan.needs_review)
        self.assertIn("audio_too_short", plan.warnings)

    def test_segmented_alignment_does_not_trim_same_length_cover_like_audio(self) -> None:
        base = _phrase([220, 247, 294, 330, 392, 440, 494, 523, 587, 659], 0.22)
        t = np.arange(base.size, dtype=np.float32) / SR
        alternate_vocal_tone = 0.22 * np.sin(2 * np.pi * 735.0 * t)
        alternate_vocal_tone *= 0.5 + 0.5 * np.sin(2 * np.pi * 5.3 * t) ** 2
        cover_like_reference = np.clip(base * 0.82 + alternate_vocal_tone.astype(np.float32), -1.0, 1.0)
        game_audio = np.clip(base * 0.95, -1.0, 1.0)

        plan = build_alignment_plan(
            cover_like_reference,
            game_audio,
            AlignmentResult(0.0, 1.0, 0),
            sr=SR,
        )

        self.assertEqual(plan.method, "global")
        self.assertEqual(plan.trimmed_segment_count, 0)
        self.assertEqual(plan.trim_total_s, 0.0)
        self.assertIn("no_reference_extra_duration", plan.warnings)


if __name__ == "__main__":
    unittest.main()
