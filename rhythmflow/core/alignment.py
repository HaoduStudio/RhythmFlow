from __future__ import annotations

import importlib.util
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy import signal
from scipy.io import wavfile

from rhythmflow.config import FMAX, FMIN, HOP, N_FFT, SR
from rhythmflow.core.audio_io import decode_mono


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlignmentResult:
    offset_s: float
    confidence: float
    lag_frames: int


@dataclass(frozen=True)
class AudioOffsetResult:
    offset_s: float
    confidence: float
    lag_samples: int
    sample_rate: int


@dataclass(frozen=True)
class SeparationResult:
    game_bgm_path: str
    tap_clicks_path: str
    sample_rate: int
    backend: str


@dataclass(frozen=True)
class TapFeatures:
    timestamps: np.ndarray
    strengths: np.ndarray
    onset_envelope: np.ndarray
    sr: int
    hop_length: int


@dataclass(frozen=True)
class FeatureAlignmentResult:
    offset_s: float
    adjustment_s: float
    score: float
    matched_taps: int
    evaluated_candidates: int


class AudioAligner:
    def __init__(
        self,
        *,
        target_sr: int = 44100,
        correlation_sr: int = SR,
        tap_hop_length: int = 128,
        output_dir: str | Path | None = None,
        separation_backend: str = "hpss",
        demucs_model: str = "htdemucs",
        tap_highpass_hz: float = 1200.0,
        fine_tune_window_ms: float = 50.0,
        fine_tune_step_ms: float = 1.0,
        match_tolerance_ms: float = 18.0,
    ) -> None:
        if target_sr <= 0 or correlation_sr <= 0:
            raise ValueError("Sample rates must be positive")
        if tap_hop_length <= 0:
            raise ValueError("tap_hop_length must be positive")
        if separation_backend not in {"hpss", "demucs", "auto"}:
            raise ValueError("separation_backend must be one of: hpss, demucs, auto")

        self.target_sr = int(target_sr)
        self.correlation_sr = int(correlation_sr)
        self.tap_hop_length = int(tap_hop_length)
        self.output_dir = Path(output_dir).expanduser() if output_dir is not None else None
        self.separation_backend = separation_backend
        self.demucs_model = demucs_model
        self.tap_highpass_hz = float(tap_highpass_hz)
        self.fine_tune_window_ms = float(fine_tune_window_ms)
        self.fine_tune_step_ms = float(fine_tune_step_ms)
        self.match_tolerance_ms = float(match_tolerance_ms)

    def separate_audio(self, video_audio_path: str | Path) -> SeparationResult:
        source = Path(video_audio_path).expanduser()
        if not source.exists():
            logger.error("Audio separation source does not exist: %s", source)
            raise FileNotFoundError(f"Audio file does not exist: {source}")

        output_dir = self._separation_output_dir(source)
        logger.info(
            "Separating audio: source=%s backend=%s output_dir=%s",
            source,
            self.separation_backend,
            output_dir,
        )
        if self.separation_backend == "demucs":
            return self._separate_with_demucs(source, output_dir)
        if self.separation_backend == "auto" and self._demucs_available():
            return self._separate_with_demucs(source, output_dir)
        return self._separate_with_hpss(source, output_dir)

    def extract_tap_features(self, tap_audio_path: str | Path) -> TapFeatures:
        logger.info("Extracting tap features from %s", tap_audio_path)
        y = self._load_audio_mono(tap_audio_path, self.target_sr)
        y = self._extract_click_band(y, self.target_sr)

        try:
            import librosa
        except ImportError:
            return self._extract_tap_features_scipy(y, self.target_sr)

        hop = self.tap_hop_length
        fmax = min(16000.0, self.target_sr * 0.45)

        onset_env = librosa.onset.onset_strength(
            y=y,
            sr=self.target_sr,
            hop_length=hop,
            n_fft=1024,
            aggregate=np.max,
            fmin=min(self.tap_highpass_hz, fmax * 0.8),
            fmax=fmax,
            lag=1,
            max_size=1,
        )
        onset_env = np.asarray(onset_env, dtype=np.float32)
        if onset_env.size == 0:
            return self._empty_tap_features(self.target_sr, hop)

        onset_frames = librosa.onset.onset_detect(
            onset_envelope=onset_env,
            sr=self.target_sr,
            hop_length=hop,
            units="frames",
            backtrack=True,
            energy=onset_env,
            pre_max=2,
            post_max=2,
            pre_avg=6,
            post_avg=6,
            delta=0.08,
            wait=1,
        )
        onset_frames = np.asarray(onset_frames, dtype=np.int64)
        timestamps = librosa.frames_to_time(
            onset_frames,
            sr=self.target_sr,
            hop_length=hop,
        ).astype(np.float32)
        strengths = self._strengths_at_frames(onset_env, onset_frames)
        logger.info("Extracted %d tap feature(s) from %s", timestamps.size, tap_audio_path)
        return TapFeatures(
            timestamps=timestamps,
            strengths=strengths,
            onset_envelope=onset_env,
            sr=self.target_sr,
            hop_length=hop,
        )

    def calculate_offset(
        self,
        game_bgm_path: str | Path,
        reference_audio_path: str | Path,
    ) -> AudioOffsetResult:
        logger.info(
            "Calculating audio offset: game=%s reference=%s",
            game_bgm_path,
            reference_audio_path,
        )
        game = self._load_audio_mono(game_bgm_path, self.correlation_sr)
        reference = self._load_audio_mono(reference_audio_path, self.correlation_sr)

        game = self._prepare_correlation_signal(game, self.correlation_sr, "game_bgm")
        reference = self._prepare_correlation_signal(
            reference,
            self.correlation_sr,
            "reference_audio",
        )

        corr = signal.correlate(game, reference, mode="full", method="fft")
        corr = np.nan_to_num(corr, copy=False)
        if corr.size == 0 or not np.any(np.isfinite(corr)):
            raise ValueError("Correlation curve contains no finite values")

        peak_index = int(np.argmax(corr))
        peak_value = float(corr[peak_index])
        lags = signal.correlation_lags(game.size, reference.size, mode="full")
        lag_samples = int(lags[peak_index])
        offset_s = lag_samples / float(self.correlation_sr)
        confidence = _confidence(corr, peak_index, peak_value)
        logger.info(
            "Calculated audio offset: offset=%.3f confidence=%.2f lag_samples=%d",
            offset_s,
            confidence,
            lag_samples,
        )
        return AudioOffsetResult(
            offset_s=offset_s,
            confidence=confidence,
            lag_samples=lag_samples,
            sample_rate=self.correlation_sr,
        )

    def fine_tune_with_features(
        self,
        base_offset: float | AlignmentResult | AudioOffsetResult,
        tap_features: TapFeatures | Sequence[float] | Mapping[str, Any],
        chart_data: Sequence[float] | Mapping[str, Any] | TapFeatures,
    ) -> FeatureAlignmentResult:
        base_offset_s = self._coerce_offset_seconds(base_offset)
        logger.info("Fine tuning offset from base %.3f", base_offset_s)
        tap_times, tap_strengths = self._coerce_tap_features(tap_features)
        chart_times = self._extract_chart_times(chart_data)

        if tap_times.size == 0:
            raise ValueError("No tap timestamps available for fine tuning")
        if chart_times.size == 0:
            raise ValueError("No chart timestamps available for fine tuning")

        half_window = max(0.0, self.fine_tune_window_ms / 1000.0)
        step = max(0.0005, self.fine_tune_step_ms / 1000.0)
        candidate_count = int(round((half_window * 2.0) / step)) + 1
        candidates = base_offset_s + np.linspace(-half_window, half_window, candidate_count)

        scores = np.empty(candidates.size, dtype=np.float32)
        matches = np.empty(candidates.size, dtype=np.int32)
        for index, candidate in enumerate(candidates):
            score, matched = self._score_feature_offset(
                float(candidate),
                tap_times,
                tap_strengths,
                chart_times,
            )
            scores[index] = score
            matches[index] = matched

        tie_break = np.abs(candidates - base_offset_s) * 1e-7
        best_index = int(np.argmax(scores - tie_break))
        best_offset = float(candidates[best_index])

        if 0 < best_index < candidates.size - 1:
            refined = self._quadratic_peak_refine(candidates, scores, best_index)
            if refined is not None and abs(refined - base_offset_s) <= half_window + step:
                best_offset = refined

        best_score, best_matches = self._score_feature_offset(
            best_offset,
            tap_times,
            tap_strengths,
            chart_times,
        )
        logger.info(
            "Fine tune result: offset=%.3f adjustment=%.3f score=%.4f matches=%d candidates=%d",
            best_offset,
            best_offset - base_offset_s,
            best_score,
            best_matches,
            candidates.size,
        )
        return FeatureAlignmentResult(
            offset_s=best_offset,
            adjustment_s=best_offset - base_offset_s,
            score=best_score,
            matched_taps=best_matches,
            evaluated_candidates=int(candidates.size),
        )

    def _separation_output_dir(self, source: Path) -> Path:
        if self.output_dir is None:
            output_dir = source.with_name(f"{source.stem}_separated")
        else:
            output_dir = self.output_dir / source.stem
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _separate_with_hpss(self, source: Path, output_dir: Path) -> SeparationResult:
        logger.info("Separating audio with HPSS/scipy backend: %s", source)
        y = self._load_audio_mono(source, self.target_sr)
        backend = "scipy_transient_filter"

        try:
            import librosa

            harmonic, percussive = librosa.effects.hpss(
                y,
                kernel_size=(31, 9),
                margin=(1.0, 5.0),
            )
            tap = self._extract_click_band(percussive, self.target_sr)
            game = harmonic + 0.35 * (percussive - tap)
            backend = "librosa_hpss"
        except Exception:
            logger.debug("librosa HPSS failed; using scipy transient filter", exc_info=True)
            tap = self._extract_click_band(y, self.target_sr)
            game = y - 0.85 * tap

        game_path = output_dir / "game_bgm.wav"
        tap_path = output_dir / "tap_clicks.wav"
        self._write_wav(game_path, game, self.target_sr)
        self._write_wav(tap_path, tap, self.target_sr)
        logger.info("Audio separated with %s: game=%s tap=%s", backend, game_path, tap_path)
        return SeparationResult(
            game_bgm_path=str(game_path),
            tap_clicks_path=str(tap_path),
            sample_rate=self.target_sr,
            backend=backend,
        )

    def _separate_with_demucs(self, source: Path, output_dir: Path) -> SeparationResult:
        logger.info("Separating audio with Demucs model=%s source=%s", self.demucs_model, source)
        try:
            from demucs.separate import main as demucs_main
        except ImportError as exc:
            logger.error("Demucs is not installed")
            raise RuntimeError("Demucs is not installed; use separation_backend='hpss'") from exc

        demucs_out = output_dir / "demucs"
        demucs_out.mkdir(parents=True, exist_ok=True)
        try:
            demucs_main(
                [
                    "--name",
                    self.demucs_model,
                    "--out",
                    str(demucs_out),
                    str(source),
                ]
            )
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"Demucs separation failed with exit code {exc.code}") from exc

        stem_dir = demucs_out / self.demucs_model / source.stem
        stem_paths = sorted(stem_dir.glob("*.wav"))
        if not stem_paths:
            logger.error("Demucs did not produce WAV stems in %s", stem_dir)
            raise RuntimeError(f"Demucs did not produce WAV stems in {stem_dir}")

        original = self._load_audio_mono(source, self.target_sr)
        stems = {
            path.stem.lower(): self._fit_length(
                self._load_audio_mono(path, self.target_sr),
                original.size,
            )
            for path in stem_paths
        }

        game_parts = [audio for name, audio in stems.items() if name != "drums"]
        game = np.sum(np.vstack(game_parts), axis=0) if game_parts else original.copy()
        tap_seed = stems.get("drums", original - game)
        tap = self._extract_click_band(tap_seed, self.target_sr)

        game_path = output_dir / "game_bgm.wav"
        tap_path = output_dir / "tap_clicks.wav"
        self._write_wav(game_path, game, self.target_sr)
        self._write_wav(tap_path, tap, self.target_sr)
        logger.info("Audio separated with Demucs: game=%s tap=%s", game_path, tap_path)
        return SeparationResult(
            game_bgm_path=str(game_path),
            tap_clicks_path=str(tap_path),
            sample_rate=self.target_sr,
            backend=f"demucs:{self.demucs_model}",
        )

    def _load_audio_mono(self, path: str | Path, sr: int) -> np.ndarray:
        source = Path(path).expanduser()
        if not source.exists():
            logger.error("Audio file does not exist: %s", source)
            raise FileNotFoundError(f"Audio file does not exist: {source}")

        try:
            import librosa

            audio, _ = librosa.load(str(source), sr=sr, mono=True)
        except Exception:
            try:
                audio = self._load_with_pydub(source, sr)
            except Exception:
                audio = decode_mono(str(source), sr=sr)

        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim != 1:
            audio = np.mean(audio, axis=0, dtype=np.float32)
        if audio.size == 0:
            raise ValueError(f"No decodable audio found in {source}")
        return np.nan_to_num(audio, copy=False)

    def _load_with_pydub(self, path: Path, sr: int) -> np.ndarray:
        from pydub import AudioSegment

        segment = AudioSegment.from_file(str(path))
        segment = segment.set_channels(1).set_frame_rate(sr)
        samples = np.asarray(segment.get_array_of_samples(), dtype=np.float32)
        max_value = float(1 << (8 * segment.sample_width - 1))
        if max_value <= 0:
            raise ValueError("Invalid pydub sample width")
        return samples / max_value

    def _extract_click_band(self, y: np.ndarray, sr: int) -> np.ndarray:
        audio = np.asarray(y, dtype=np.float32)
        if audio.size < 4:
            return audio.copy()

        highpass = min(max(80.0, self.tap_highpass_hz), sr * 0.45)
        sos = signal.butter(4, highpass, btype="highpass", fs=sr, output="sos")
        if audio.size > 3 * sos.shape[0] * 2:
            filtered = signal.sosfiltfilt(sos, audio).astype(np.float32)
        else:
            filtered = signal.sosfilt(sos, audio).astype(np.float32)

        frame = max(16, int(sr * 0.006))
        kernel = np.ones(frame, dtype=np.float32) / frame
        rms_power = signal.fftconvolve(filtered * filtered, kernel, mode="same")
        rms = np.sqrt(np.maximum(rms_power, 0.0))
        median = float(np.median(rms))
        mad = float(np.median(np.abs(rms - median)))
        threshold = median + max(3.0 * 1.4826 * mad, 1e-5)
        gate = np.clip((rms - threshold) / (threshold + 1e-8), 0.0, 1.0)
        return (filtered * gate).astype(np.float32, copy=False)

    def _extract_tap_features_scipy(self, y: np.ndarray, sr: int) -> TapFeatures:
        hop = self.tap_hop_length
        frame_length = max(4 * hop, int(sr * 0.012))
        if y.size < frame_length:
            return self._empty_tap_features(sr, hop)

        squared = y * y
        window = np.ones(frame_length, dtype=np.float32) / frame_length
        energy_power = signal.fftconvolve(squared, window, mode="same")
        energy = np.sqrt(np.maximum(energy_power, 0.0))
        frame_count = max(1, int(np.ceil(energy.size / hop)))
        padded = np.pad(energy, (0, frame_count * hop - energy.size))
        envelope = padded.reshape(frame_count, hop).max(axis=1)
        flux = np.maximum(np.diff(envelope, prepend=envelope[:1]), 0.0)

        median = float(np.median(flux))
        mad = float(np.median(np.abs(flux - median)))
        prominence = max(3.0 * 1.4826 * mad, 1e-6)
        min_distance = max(1, int(round(0.012 * sr / hop)))
        frames, _ = signal.find_peaks(
            flux,
            distance=min_distance,
            prominence=prominence,
        )
        frames = frames.astype(np.int64, copy=False)
        timestamps = (frames * hop / float(sr)).astype(np.float32)
        strengths = self._strengths_at_frames(flux.astype(np.float32), frames)
        return TapFeatures(
            timestamps=timestamps,
            strengths=strengths,
            onset_envelope=flux.astype(np.float32, copy=False),
            sr=sr,
            hop_length=hop,
        )

    def _prepare_correlation_signal(self, y: np.ndarray, sr: int, name: str) -> np.ndarray:
        audio = np.asarray(y, dtype=np.float32)
        audio = np.nan_to_num(audio, copy=False)
        if audio.size < max(16, sr // 20):
            raise ValueError(f"{name} is too short for correlation")

        audio = audio - float(np.mean(audio))
        cutoff = min(40.0, sr * 0.2)
        sos = signal.butter(2, cutoff, btype="highpass", fs=sr, output="sos")
        if audio.size > 3 * sos.shape[0] * 2:
            audio = signal.sosfiltfilt(sos, audio).astype(np.float32)
        else:
            audio = signal.sosfilt(sos, audio).astype(np.float32)

        rms = float(np.sqrt(np.mean(audio * audio)))
        if rms <= 1e-8:
            raise ValueError(f"{name} has near-zero energy after preprocessing")
        return (audio / rms).astype(np.float32, copy=False)

    def _coerce_offset_seconds(
        self,
        base_offset: float | AlignmentResult | AudioOffsetResult,
    ) -> float:
        if isinstance(base_offset, (AlignmentResult, AudioOffsetResult)):
            return float(base_offset.offset_s)
        return float(base_offset)

    def _coerce_tap_features(
        self,
        tap_features: TapFeatures | Sequence[float] | Mapping[str, Any],
    ) -> tuple[np.ndarray, np.ndarray]:
        if isinstance(tap_features, TapFeatures):
            times = np.asarray(tap_features.timestamps, dtype=np.float64)
            strengths = np.asarray(tap_features.strengths, dtype=np.float64)
        elif isinstance(tap_features, Mapping):
            raw_times = None
            for key in ("timestamps", "timestamps_s", "times", "onsets"):
                if key in tap_features:
                    raw_times = tap_features[key]
                    break
            if raw_times is None:
                raise ValueError("tap_features mapping must include timestamps")
            times = np.asarray(raw_times, dtype=np.float64)
            raw_strengths = tap_features.get("strengths")
            strengths = (
                np.asarray(raw_strengths, dtype=np.float64)
                if raw_strengths is not None
                else np.ones(times.shape, dtype=np.float64)
            )
        else:
            times = np.asarray(tap_features, dtype=np.float64)
            strengths = np.ones(times.shape, dtype=np.float64)

        times = np.ravel(times)
        strengths = np.ravel(strengths)
        if strengths.size != times.size:
            strengths = np.ones(times.shape, dtype=np.float64)
        valid = np.isfinite(times) & np.isfinite(strengths)
        times = times[valid]
        strengths = strengths[valid]
        if times.size == 0:
            return times, strengths

        strengths = np.maximum(strengths, 0.0)
        if float(np.max(strengths)) <= 0.0:
            strengths = np.ones(times.shape, dtype=np.float64)
        order = np.argsort(times)
        return times[order], strengths[order]

    def _extract_chart_times(
        self,
        chart_data: Sequence[float] | Mapping[str, Any] | TapFeatures,
    ) -> np.ndarray:
        values = self._collect_time_values(chart_data, scale=1.0)
        arr = np.asarray(values, dtype=np.float64)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return arr

        if float(np.nanmedian(arr)) > 1000.0:
            arr = arr / 1000.0
        return np.unique(np.sort(arr))

    def _collect_time_values(self, value: Any, *, scale: float) -> list[float]:
        if value is None:
            return []
        if isinstance(value, TapFeatures):
            return [float(item) for item in np.ravel(value.timestamps)]
        if isinstance(value, Mapping):
            local_scale = self._scale_from_unit(value, scale)
            for key in ("notes", "chart", "events", "objects", "timestamps", "onsets"):
                if key in value:
                    return self._collect_time_values(value[key], scale=local_scale)

            for key in ("time_ms", "timestamp_ms", "start_ms", "ms"):
                if key in value:
                    return [float(value[key]) * 0.001]
            for key in ("time_s", "timestamp_s", "start_s", "seconds"):
                if key in value:
                    return [float(value[key])]
            for key in ("time", "timestamp", "start", "beat_time"):
                if key in value:
                    return [float(value[key]) * local_scale]
            return []

        if isinstance(value, np.ndarray):
            if value.ndim == 0:
                return [float(value) * scale]
            if value.ndim == 1:
                return [float(item) * scale for item in value]
            return [float(item) * scale for item in value[:, 0]]

        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            items: list[float] = []
            for item in value:
                items.extend(self._collect_time_values(item, scale=scale))
            return items

        return [float(value) * scale]

    def _scale_from_unit(self, value: Mapping[str, Any], default: float) -> float:
        unit = value.get("unit") or value.get("time_unit") or value.get("timeUnit")
        if isinstance(unit, str) and unit.lower() in {"ms", "millisecond", "milliseconds"}:
            return 0.001
        if isinstance(unit, str) and unit.lower() in {"s", "sec", "second", "seconds"}:
            return 1.0
        return default

    def _score_feature_offset(
        self,
        candidate_offset: float,
        tap_times: np.ndarray,
        tap_strengths: np.ndarray,
        chart_times: np.ndarray,
    ) -> tuple[float, int]:
        shifted_chart = chart_times + candidate_offset
        if shifted_chart.size == 0:
            return 0.0, 0

        tolerance = max(0.001, self.match_tolerance_ms / 1000.0)
        sigma = max(tolerance / 2.5, 0.001)
        positions = np.searchsorted(shifted_chart, tap_times)

        distances = np.full(tap_times.shape, np.inf, dtype=np.float64)
        right_mask = positions < shifted_chart.size
        distances[right_mask] = np.minimum(
            distances[right_mask],
            np.abs(tap_times[right_mask] - shifted_chart[positions[right_mask]]),
        )
        left_mask = positions > 0
        left_positions = positions[left_mask] - 1
        distances[left_mask] = np.minimum(
            distances[left_mask],
            np.abs(tap_times[left_mask] - shifted_chart[left_positions]),
        )

        matched_mask = distances <= tolerance
        if not np.any(matched_mask):
            return 0.0, 0

        weights = tap_strengths / (float(np.max(tap_strengths)) + 1e-8)
        gaussian = np.exp(-0.5 * (distances[matched_mask] / sigma) ** 2)
        weighted_hits = float(np.sum(weights[matched_mask] * gaussian))
        weight_total = float(np.sum(weights)) + 1e-8
        coverage = float(np.count_nonzero(matched_mask)) / max(
            1,
            min(tap_times.size, shifted_chart.size),
        )
        score = (weighted_hits / weight_total) + 0.25 * coverage
        return score, int(np.count_nonzero(matched_mask))

    def _quadratic_peak_refine(
        self,
        candidates: np.ndarray,
        scores: np.ndarray,
        best_index: int,
    ) -> float | None:
        left = float(scores[best_index - 1])
        center = float(scores[best_index])
        right = float(scores[best_index + 1])
        denominator = left - 2.0 * center + right
        if abs(denominator) < 1e-12:
            return None
        step = float(candidates[1] - candidates[0])
        delta = 0.5 * (left - right) / denominator
        if abs(delta) > 1.0:
            return None
        return float(candidates[best_index] + delta * step)

    def _empty_tap_features(self, sr: int, hop: int) -> TapFeatures:
        return TapFeatures(
            timestamps=np.empty(0, dtype=np.float32),
            strengths=np.empty(0, dtype=np.float32),
            onset_envelope=np.empty(0, dtype=np.float32),
            sr=sr,
            hop_length=hop,
        )

    def _strengths_at_frames(self, envelope: np.ndarray, frames: np.ndarray) -> np.ndarray:
        if frames.size == 0:
            return np.empty(0, dtype=np.float32)
        safe_frames = np.clip(frames, 0, max(0, envelope.size - 1))
        return np.asarray(envelope[safe_frames], dtype=np.float32)

    def _fit_length(self, audio: np.ndarray, length: int) -> np.ndarray:
        arr = np.asarray(audio, dtype=np.float32)
        if arr.size == length:
            return arr
        if arr.size > length:
            return arr[:length]
        return np.pad(arr, (0, length - arr.size))

    def _write_wav(self, path: Path, audio: np.ndarray, sr: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        arr = np.asarray(audio, dtype=np.float32)
        arr = np.nan_to_num(arr, copy=False)
        peak = float(np.max(np.abs(arr))) if arr.size else 0.0
        if peak > 1.0:
            arr = arr / peak
        wavfile.write(path, sr, arr.astype(np.float32, copy=False))

    def _demucs_available(self) -> bool:
        return importlib.util.find_spec("demucs") is not None


def chromagram(
    y: np.ndarray,
    *,
    sr: int = SR,
    n_fft: int = N_FFT,
    hop: int = HOP,
    fmin: float = FMIN,
    fmax: float = FMAX,
) -> np.ndarray:
    logger.debug("Building chromagram for %d sample(s)", np.asarray(y).size)
    y = np.asarray(y, dtype=np.float32)
    if y.ndim != 1:
        raise ValueError("Expected mono audio")
    if y.size == 0:
        raise ValueError("Cannot build chromagram from empty audio")
    if y.size < n_fft:
        y = np.pad(y, (0, n_fft - y.size))

    noverlap = n_fft - hop
    freqs, _, stft = signal.stft(
        y,
        fs=sr,
        window="hann",
        nperseg=n_fft,
        noverlap=noverlap,
        boundary=None,
        padded=True,
    )
    magnitude = np.log1p(np.abs(stft)).astype(np.float32, copy=False)
    valid = (freqs >= fmin) & (freqs <= fmax)
    valid_freqs = freqs[valid]
    valid_mag = magnitude[valid]
    if valid_freqs.size == 0 or valid_mag.size == 0:
        raise ValueError("No usable FFT bins for chromagram")

    midi = 69 + 12 * np.log2(valid_freqs / 440.0)
    pitch_classes = np.mod(np.rint(midi).astype(np.int16), 12)

    chroma = np.zeros((12, valid_mag.shape[1]), dtype=np.float32)
    for pitch_class in range(12):
        rows = valid_mag[pitch_classes == pitch_class]
        if rows.size:
            chroma[pitch_class] = rows.sum(axis=0)

    chroma -= chroma.mean(axis=0, keepdims=True)
    norms = np.linalg.norm(chroma, axis=0, keepdims=True)
    chroma = np.divide(chroma, norms + 1e-8, out=np.zeros_like(chroma), where=norms > 0)
    return chroma


def find_offset(
    ref_chroma: np.ndarray,
    vid_chroma: np.ndarray,
    *,
    sr: int = SR,
    hop: int = HOP,
) -> AlignmentResult:
    logger.info("Finding chroma offset")
    ref = _validate_chroma(ref_chroma, "reference")
    vid = _validate_chroma(vid_chroma, "video")
    if ref.shape[1] < 2 or vid.shape[1] < 2:
        raise ValueError("Need at least two chroma frames for alignment")

    curves = []
    for row in range(12):
        vid_row = vid[row] - np.mean(vid[row])
        ref_row = ref[row] - np.mean(ref[row])
        curves.append(signal.fftconvolve(vid_row, ref_row[::-1], mode="full"))
    corr = np.sum(curves, axis=0)

    if not np.any(np.isfinite(corr)):
        raise ValueError("Correlation curve contains no finite values")
    peak_index = int(np.argmax(corr))
    peak_value = float(corr[peak_index])
    lag_frames = peak_index - (ref.shape[1] - 1)
    offset_s = lag_frames * hop / sr
    confidence = _confidence(corr, peak_index, peak_value)
    logger.info(
        "Found chroma offset: offset=%.3f confidence=%.2f lag_frames=%d",
        offset_s,
        confidence,
        lag_frames,
    )
    return AlignmentResult(offset_s=offset_s, confidence=confidence, lag_frames=lag_frames)


def estimate_offset(ref_audio: np.ndarray, video_audio: np.ndarray) -> AlignmentResult:
    return find_offset(chromagram(ref_audio), chromagram(video_audio))


def _validate_chroma(chroma: np.ndarray, name: str) -> np.ndarray:
    arr = np.asarray(chroma, dtype=np.float32)
    if arr.ndim != 2 or arr.shape[0] != 12:
        raise ValueError(f"{name} chroma must have shape (12, frames)")
    if arr.shape[1] == 0:
        raise ValueError(f"{name} chroma has no frames")
    return np.nan_to_num(arr, copy=False)


def _confidence(corr: np.ndarray, peak_index: int, peak_value: float) -> float:
    exclusion = 5
    mask = np.ones(corr.shape, dtype=bool)
    start = max(0, peak_index - exclusion)
    end = min(corr.size, peak_index + exclusion + 1)
    mask[start:end] = False
    side = corr[mask]
    if side.size == 0:
        return 0.0
    median = float(np.median(side))
    mad = float(np.median(np.abs(side - median)))
    robust_sigma = max(1.4826 * mad, float(np.std(side)), 1e-6)
    return max(0.0, (peak_value - median) / robust_sigma)
