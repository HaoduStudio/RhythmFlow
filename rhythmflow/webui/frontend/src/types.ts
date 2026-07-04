export type Language = 'zh' | 'en';

export type CutMode = 'accurate' | 'fast';

export interface Settings {
  language: Language;
  output_dir: string;
  output_pattern: string;
  original_volume: number;
  reference_volume: number;
  cut_mode: CutMode;
}

export interface RowState {
  video_path: string;
  file_name: string;
  analyzed: boolean;
  error: string | null;
  detected_offset: number | null;
  confidence: number | null;
  nudge: number;
  final_offset: number | null;
  smart_trim_s: number;
  smart_trim_count: number;
  smart_confidence: number | null;
  needs_review: boolean;
  review_confirmed: boolean;
  warnings: string[];
}

export interface SegmentNote {
  key: string;
  params: Record<string, string | number>;
}

export interface ReviewSegment {
  id: string;
  row: number;
  segment_index: number;
  is_global: boolean;
  file_name: string;
  video_path: string;
  reference_path: string;
  video_url: string;
  reference_url: string;
  label_key: string;
  label_params: Record<string, string | number>;
  notes: SegmentNote[];
  reference_start_s: number;
  reference_end_s: number;
  video_start_s: number;
  video_end_s: number;
}

export interface WaveformTrack {
  envelope: number[];
  window_start_s: number;
  window_duration_s: number;
}

export interface WaveformData {
  ok: boolean;
  error?: string;
  duration_s: number;
  bounds: { lower: number; upper: number };
  reference: WaveformTrack;
  video: WaveformTrack;
}

export interface AboutInfo {
  app_name: string;
  version: string;
  author: string;
  repository: string;
}

export interface ReviewDelta {
  row: number;
  segment_index: number;
  delta_s: number;
}

export interface AppContext {
  language: Language;
  reference_path: string;
  output_dir: string;
  output_pattern: string;
  original_volume: number;
  reference_volume: number;
  mode: CutMode;
}

export interface CommandResult {
  ok: boolean;
  error?: string;
  review_rows?: number[];
}
