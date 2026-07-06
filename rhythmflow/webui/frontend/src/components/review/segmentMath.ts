import type { ReviewSegment } from "../../types";

// Mirrors rhythmflow/webui/waveform.py::adjusted_spans and state._adjusted_spans.
export function segmentDuration(segment: ReviewSegment): number {
  const candidates = [
    segment.reference_end_s - segment.reference_start_s,
    segment.video_end_s - segment.video_start_s,
  ].filter((value) => value > 0);
  if (candidates.length === 0) return 0.1;
  return Math.max(0.1, Math.min(...candidates));
}

export type Spans = [number, number, number, number];

export function adjustedSpans(segment: ReviewSegment, delta: number): Spans {
  const duration = segmentDuration(segment);
  if (segment.is_global) {
    const offset = segment.video_start_s - segment.reference_start_s + delta;
    const referenceStart = Math.max(0, -offset);
    const videoStart = Math.max(0, offset);
    return [referenceStart, referenceStart + duration, videoStart, videoStart + duration];
  }
  const videoStart = Math.max(0, segment.video_start_s + delta);
  const videoEnd = Math.max(videoStart, segment.video_end_s + delta);
  return [segment.reference_start_s, segment.reference_end_s, videoStart, videoEnd];
}

export function formatSpan(start: number, end: number): string {
  return `${start.toFixed(2)}s - ${end.toFixed(2)}s`;
}

export function formatSeconds(value: number): string {
  return `${Math.max(0, value).toFixed(2)}s`;
}
