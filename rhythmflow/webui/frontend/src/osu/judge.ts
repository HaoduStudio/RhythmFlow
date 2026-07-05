import type {
  Judgement,
  JudgementEvent,
  ManiaChart,
  NoteHit,
  ReplayData,
  SimResult,
  SimSample,
} from './types';

interface Windows {
  max: number;
  perfect: number;
  great: number;
  good: number;
  bad: number;
  miss: number;
}

const JUDGEMENT_VALUE: Record<Judgement, number> = {
  max: 300,
  perfect: 300,
  great: 200,
  good: 100,
  bad: 50,
  miss: 0,
};

function hitWindows(overallDifficulty: number): Windows {
  const od = overallDifficulty;
  return {
    max: 16,
    perfect: 64 - 3 * od,
    great: 97 - 3 * od,
    good: 127 - 3 * od,
    bad: 151 - 3 * od,
    miss: 188 - 3 * od,
  };
}

function judgeInWindow(dt: number, w: Windows): Judgement {
  const a = Math.abs(dt);
  if (a <= w.max) return 'max';
  if (a <= w.perfect) return 'perfect';
  if (a <= w.great) return 'great';
  if (a <= w.good) return 'good';
  if (a <= w.bad) return 'bad';
  return 'miss';
}

export function simulateReplay(chart: ManiaChart, replay: ReplayData): SimResult {
  const w = hitWindows(chart.overallDifficulty);
  const raw: JudgementEvent[] = [];
  const hitByColumn: Array<Array<NoteHit | null>> = chart.notesByColumn.map((notes) =>
    new Array<NoteHit | null>(notes.length).fill(null),
  );

  for (let column = 0; column < chart.keyCount; column += 1) {
    const notes = chart.notesByColumn[column] ?? [];
    const intervals = replay.pressIntervals[column] ?? [];
    let ii = 0;
    for (let ni = 0; ni < notes.length; ni += 1) {
      const note = notes[ni];
      while (ii < intervals.length && intervals[ii].start < note.startTime - w.miss) ii += 1;
      if (ii < intervals.length && intervals[ii].start <= note.startTime + w.miss) {
        const press = intervals[ii].start;
        const judgement = judgeInWindow(press - note.startTime, w);
        raw.push({ time: press, column, judgement });
        hitByColumn[column][ni] = { time: press, judgement };
        ii += 1;
      } else {
        const time = note.startTime + w.miss;
        raw.push({ time, column, judgement: 'miss' });
        hitByColumn[column][ni] = { time, judgement: 'miss' };
      }
    }
  }

  raw.sort((a, b) => a.time - b.time);

  const times: number[] = [];
  const comboAt: number[] = [];
  const accAt: number[] = [];
  const scoreAt: number[] = [];
  const counts: Record<Judgement, number> = {
    max: 0,
    perfect: 0,
    great: 0,
    good: 0,
    bad: 0,
    miss: 0,
  };

  let combo = 0;
  let maxCombo = 0;
  let weighted = 0;
  let weightMax = 0;
  let rawScore = 0;

  for (const event of raw) {
    counts[event.judgement] += 1;
    if (event.judgement === 'miss') combo = 0;
    else combo += 1;
    maxCombo = Math.max(maxCombo, combo);
    weighted += JUDGEMENT_VALUE[event.judgement];
    weightMax += 300;
    rawScore += JUDGEMENT_VALUE[event.judgement] * (1 + combo * 0.02);
    times.push(event.time);
    comboAt.push(combo);
    accAt.push(weightMax > 0 ? weighted / weightMax : 1);
    scoreAt.push(rawScore);
  }

  const target = replay.stats.totalScore;
  if (target > 0 && rawScore > 0) {
    const k = target / rawScore;
    for (let i = 0; i < scoreAt.length; i += 1) scoreAt[i] = Math.round(scoreAt[i] * k);
  }

  return { events: raw, hitByColumn, times, comboAt, accAt, scoreAt, counts, maxCombo };
}

function upperBound(values: number[], target: number): number {
  let lo = 0;
  let hi = values.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (values[mid] <= target) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

export function sampleSim(sim: SimResult, timeMs: number): SimSample {
  const idx = upperBound(sim.times, timeMs) - 1;
  if (idx < 0) {
    return { combo: 0, accuracy: 1, score: 0, judgement: null, judgementAge: Infinity };
  }
  return {
    combo: sim.comboAt[idx],
    accuracy: sim.accAt[idx],
    score: sim.scoreAt[idx],
    judgement: sim.events[idx].judgement,
    judgementAge: timeMs - sim.times[idx],
  };
}
