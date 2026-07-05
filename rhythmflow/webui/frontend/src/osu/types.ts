export type Judgement = 'max' | 'perfect' | 'great' | 'good' | 'bad' | 'miss';

export interface ManiaNote {
  column: number;
  startTime: number;
  endTime: number | null;
}

export interface ManiaChart {
  keyCount: number;
  notes: ManiaNote[];
  notesByColumn: ManiaNote[][];
  overallDifficulty: number;
  durationMs: number;
  title: string;
  artist: string;
  creator: string;
  version: string;
  audioFilename: string;
  backgroundFilename: string | null;
}

export interface PressInterval {
  start: number;
  end: number;
}

export interface ReplayStats {
  playerName: string;
  count320: number;
  count300: number;
  count200: number;
  count100: number;
  count50: number;
  countMiss: number;
  maxCombo: number;
  totalScore: number;
  accuracy: number;
  mods: string[];
}

export interface ReplayData {
  keyCount: number;
  pressIntervals: PressInterval[][];
  stats: ReplayStats;
  rate: number;
}

export interface JudgementEvent {
  time: number;
  column: number;
  judgement: Judgement;
}

export interface NoteHit {
  time: number;
  judgement: Judgement;
}

export interface SimResult {
  events: JudgementEvent[];
  hitByColumn: Array<Array<NoteHit | null>>;
  times: number[];
  comboAt: number[];
  accAt: number[];
  scoreAt: number[];
  counts: Record<Judgement, number>;
  maxCombo: number;
}

export interface SimSample {
  combo: number;
  accuracy: number;
  score: number;
  judgement: Judgement | null;
  judgementAge: number;
}

export interface RenderScene {
  chart: ManiaChart;
  replay: ReplayData | null;
  sim: SimResult | null;
  background: ImageBitmap | null;
}

export interface RenderOptions {
  scrollSpeed: number;
  showHud: boolean;
}

export interface ExportConfig {
  width: number;
  height: number;
  fps: number;
  container: 'mp4' | 'webm';
}

export interface OszDifficulty {
  filename: string;
  osuText: string;
  keyCount: number;
  title: string;
  version: string;
  mode: number;
}

export interface OszContent {
  difficulties: OszDifficulty[];
  files: Record<string, Uint8Array>;
}
