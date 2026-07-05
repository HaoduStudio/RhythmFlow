import { strFromU8, unzipSync } from 'fflate';
import { BeatmapDecoder, HoldableObject, ScoreDecoder } from 'osu-parsers';
import type { Beatmap, LegacyReplayFrame } from 'osu-classes';
import type {
  ManiaChart,
  ManiaNote,
  OszContent,
  OszDifficulty,
  PressInterval,
  ReplayData,
  ReplayStats,
} from './types';

const MANIA_MODE = 3;

function basename(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function columnFor(startX: number, keyCount: number): number {
  const raw = Math.floor((startX * keyCount) / 512);
  return Math.min(keyCount - 1, Math.max(0, raw));
}

function keyCountOf(beatmap: Beatmap): number {
  return Math.max(1, Math.round(beatmap.difficulty.circleSize));
}

export async function loadOsz(file: File): Promise<OszContent> {
  const buffer = new Uint8Array(await file.arrayBuffer());
  const files = unzipSync(buffer);
  const decoder = new BeatmapDecoder();
  const difficulties: OszDifficulty[] = [];

  for (const [name, data] of Object.entries(files)) {
    if (!name.toLowerCase().endsWith('.osu')) continue;
    const osuText = strFromU8(data);
    let beatmap: Beatmap;
    try {
      beatmap = decoder.decodeFromString(osuText, {
        parseStoryboard: false,
        parseColours: false,
        parseEvents: false,
      });
    } catch {
      continue;
    }
    if (beatmap.mode !== MANIA_MODE) continue;
    difficulties.push({
      filename: name,
      osuText,
      keyCount: keyCountOf(beatmap),
      title: beatmap.metadata.title || beatmap.metadata.titleUnicode,
      version: beatmap.metadata.version,
      mode: beatmap.mode,
    });
  }

  difficulties.sort((a, b) => a.keyCount - b.keyCount || a.version.localeCompare(b.version));
  return { difficulties, files };
}

export function buildChart(osuText: string): ManiaChart {
  const beatmap = new BeatmapDecoder().decodeFromString(osuText, { parseStoryboard: false });
  const keyCount = keyCountOf(beatmap);
  const notes: ManiaNote[] = beatmap.hitObjects.map((obj) => ({
    column: columnFor(obj.startX, keyCount),
    startTime: Math.round(obj.startTime),
    endTime: obj instanceof HoldableObject ? Math.round(obj.endTime) : null,
  }));
  notes.sort((a, b) => a.startTime - b.startTime);

  const notesByColumn: ManiaNote[][] = Array.from({ length: keyCount }, () => []);
  for (const note of notes) notesByColumn[note.column].push(note);

  const durationMs = notes.reduce((max, n) => Math.max(max, n.endTime ?? n.startTime), 0);

  return {
    keyCount,
    notes,
    notesByColumn,
    overallDifficulty: beatmap.difficulty.overallDifficulty,
    durationMs,
    title: beatmap.metadata.title || beatmap.metadata.titleUnicode,
    artist: beatmap.metadata.artist || beatmap.metadata.artistUnicode,
    creator: beatmap.metadata.creator,
    version: beatmap.metadata.version,
    audioFilename: beatmap.general.audioFilename,
    backgroundFilename: beatmap.events.backgroundPath ?? null,
  };
}

export function findFile(content: OszContent, name: string | null): Uint8Array | null {
  if (!name) return null;
  const target = basename(name).toLowerCase();
  for (const [key, data] of Object.entries(content.files)) {
    if (basename(key).toLowerCase() === target) return data;
  }
  return null;
}

const MOD_FLAGS: Array<[number, string]> = [
  [2, 'EZ'],
  [8, 'HD'],
  [16, 'HR'],
  [64, 'DT'],
  [256, 'HT'],
  [512, 'NC'],
  [1024, 'FL'],
  [4096, 'FI'],
  [1 << 20, 'MR'],
];

function decodeMods(rawMods: number): string[] {
  const mods = MOD_FLAGS.filter(([flag]) => (rawMods & flag) !== 0).map(([, name]) => name);
  if (mods.includes('NC')) return mods.filter((m) => m !== 'DT');
  return mods;
}

function rateFromMods(mods: string[]): number {
  if (mods.includes('DT') || mods.includes('NC')) return 1.5;
  if (mods.includes('HT')) return 0.75;
  return 1;
}

export async function parseReplay(file: File, keyCount: number): Promise<ReplayData> {
  const buffer = new Uint8Array(await file.arrayBuffer());
  const score = await new ScoreDecoder().decodeFromBuffer(buffer, true);
  const info = score.info;
  const mask = (1 << keyCount) - 1;

  const frames = ((score.replay?.frames ?? []) as LegacyReplayFrame[])
    .filter((f) => Number.isFinite(f.startTime) && f.startTime >= 0 && f.mouseX >= 0)
    .sort((a, b) => a.startTime - b.startTime);

  const pressIntervals: PressInterval[][] = Array.from({ length: keyCount }, () => []);
  const openStart: Array<number | null> = new Array(keyCount).fill(null);
  let prevMask = 0;
  let lastTime = 0;

  for (const frame of frames) {
    const state = (frame.mouseX | 0) & mask;
    for (let c = 0; c < keyCount; c += 1) {
      const now = (state >> c) & 1;
      const was = (prevMask >> c) & 1;
      if (now && !was) {
        openStart[c] = frame.startTime;
      } else if (!now && was && openStart[c] !== null) {
        pressIntervals[c].push({ start: openStart[c] as number, end: frame.startTime });
        openStart[c] = null;
      }
    }
    prevMask = state;
    lastTime = frame.startTime;
  }
  for (let c = 0; c < keyCount; c += 1) {
    if (openStart[c] !== null) pressIntervals[c].push({ start: openStart[c] as number, end: lastTime });
  }

  const mods = decodeMods((info as { rawMods?: number }).rawMods ?? 0);
  const stats: ReplayStats = {
    playerName: (info as { username?: string }).username ?? '',
    count320: info.countGeki,
    count300: info.count300,
    count200: info.countKatu,
    count100: info.count100,
    count50: info.count50,
    countMiss: info.countMiss,
    maxCombo: info.maxCombo,
    totalScore: info.totalScore,
    accuracy: info.accuracy,
    mods,
  };

  return { keyCount, pressIntervals, stats, rate: rateFromMods(mods) };
}
