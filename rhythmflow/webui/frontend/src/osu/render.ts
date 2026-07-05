import { sampleSim } from './judge';
import type { Judgement, ManiaNote, NoteHit, PressInterval, RenderOptions, RenderScene } from './types';

type Ctx2D = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

const JUDGEMENT_TEXT: Record<Judgement, string> = {
  max: '300',
  perfect: '300',
  great: '200',
  good: '100',
  bad: '50',
  miss: 'MISS',
};

const JUDGEMENT_COLOR: Record<Judgement, string> = {
  max: '#b9f2ff',
  perfect: '#ffd94a',
  great: '#4ade80',
  good: '#60a5fa',
  bad: '#cbd5e1',
  miss: '#f87171',
};

const MAX_RAINBOW = ['#ff6b6b', '#ffb54a', '#ffe66d', '#6ee77a', '#5ad0f0', '#b78bff'];

interface Layout {
  fieldLeft: number;
  fieldWidth: number;
  colWidth: number;
  receptorY: number;
  noteH: number;
  pxPerMs: number;
}

function computeLayout(width: number, height: number, keyCount: number, scrollSpeed: number): Layout {
  const colWidth = clamp(
    Math.min(Math.floor((width * 0.9) / keyCount), Math.floor(height * 0.11)),
    24,
    160,
  );
  const fieldWidth = colWidth * keyCount;
  return {
    fieldLeft: Math.round((width - fieldWidth) / 2),
    fieldWidth,
    colWidth,
    receptorY: Math.round(height * 0.86),
    noteH: clamp(Math.round(colWidth * 0.34), 10, 44),
    pxPerMs: scrollSpeed * (height / 720) * 0.06,
  };
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value));
}

function noteColor(column: number, keyCount: number): string {
  if (keyCount % 2 === 1 && column === (keyCount - 1) / 2) return '#facc15';
  return column % 2 === 0 ? '#e2e8f0' : '#57b6f5';
}

function roundRect(ctx: Ctx2D, x: number, y: number, w: number, h: number, r: number): void {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function lowerBoundStart(notes: ManiaNote[], time: number): number {
  let lo = 0;
  let hi = notes.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (notes[mid].startTime < time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

function pressedAt(intervals: PressInterval[], time: number): boolean {
  let lo = 0;
  let hi = intervals.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (intervals[mid].start <= time) lo = mid + 1;
    else hi = mid;
  }
  const idx = lo - 1;
  return idx >= 0 && intervals[idx].end >= time;
}

export function drawScene(
  ctx: Ctx2D,
  width: number,
  height: number,
  scene: RenderScene,
  timeMs: number,
  options: RenderOptions,
): void {
  const { chart, replay, background } = scene;
  const layout = computeLayout(width, height, chart.keyCount, options.scrollSpeed);

  ctx.fillStyle = '#05070d';
  ctx.fillRect(0, 0, width, height);
  if (background) drawBackground(ctx, width, height, background);

  drawField(ctx, height, layout, chart.keyCount);
  if (replay) drawColumnLights(ctx, layout, chart.keyCount, replay.pressIntervals, timeMs);
  drawNotes(ctx, height, layout, scene, timeMs);
  drawReceptor(ctx, layout, chart.keyCount);

  if (options.showHud) drawHud(ctx, width, height, scene, timeMs, layout);
}

function drawBackground(ctx: Ctx2D, width: number, height: number, image: ImageBitmap): void {
  const scale = Math.max(width / image.width, height / image.height);
  const w = image.width * scale;
  const h = image.height * scale;
  ctx.globalAlpha = 0.22;
  ctx.drawImage(image, (width - w) / 2, (height - h) / 2, w, h);
  ctx.globalAlpha = 1;
}

function drawField(ctx: Ctx2D, height: number, layout: Layout, keyCount: number): void {
  ctx.fillStyle = 'rgba(6, 10, 20, 0.72)';
  ctx.fillRect(layout.fieldLeft, 0, layout.fieldWidth, height);
  ctx.strokeStyle = 'rgba(148, 163, 184, 0.18)';
  ctx.lineWidth = 1;
  for (let c = 0; c <= keyCount; c += 1) {
    const x = layout.fieldLeft + c * layout.colWidth + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }
}

function drawColumnLights(
  ctx: Ctx2D,
  layout: Layout,
  keyCount: number,
  pressIntervals: PressInterval[][],
  timeMs: number,
): void {
  for (let c = 0; c < keyCount; c += 1) {
    if (!pressedAt(pressIntervals[c] ?? [], timeMs)) continue;
    const x = layout.fieldLeft + c * layout.colWidth;
    const gradient = ctx.createLinearGradient(0, 0, 0, layout.receptorY);
    gradient.addColorStop(0, 'rgba(94, 234, 212, 0)');
    gradient.addColorStop(1, 'rgba(94, 234, 212, 0.28)');
    ctx.fillStyle = gradient;
    ctx.fillRect(x, 0, layout.colWidth, layout.receptorY);
  }
}

function drawNotes(
  ctx: Ctx2D,
  height: number,
  layout: Layout,
  scene: RenderScene,
  timeMs: number,
): void {
  const { chart, sim } = scene;
  const passMs = (height - layout.receptorY + layout.noteH * 2) / layout.pxPerMs;
  const futureMs = (layout.receptorY + layout.noteH) / layout.pxPerMs;
  const lowerTime = timeMs - passMs;
  const pad = Math.max(2, Math.round(layout.colWidth * 0.08));

  for (let c = 0; c < chart.keyCount; c += 1) {
    const notes = chart.notesByColumn[c];
    if (!notes.length) continue;
    const color = noteColor(c, chart.keyCount);
    const x = layout.fieldLeft + c * layout.colWidth + pad;
    const w = layout.colWidth - pad * 2;

    let i = lowerBoundStart(notes, lowerTime);
    while (i > 0 && notes[i - 1].endTime !== null && (notes[i - 1].endTime as number) >= lowerTime) {
      i -= 1;
    }
    for (; i < notes.length; i += 1) {
      const note = notes[i];
      if (note.startTime > timeMs + futureMs) break;
      // Without a replay, behave like autoplay: consume every note on time.
      const hit: NoteHit | null = sim
        ? sim.hitByColumn[c]?.[i] ?? null
        : { time: note.startTime, judgement: 'max' };
      const consumed = hit !== null && hit.judgement !== 'miss' && timeMs >= hit.time;
      const missed = hit !== null && hit.judgement === 'miss' && timeMs >= hit.time;

      let headY = layout.receptorY - (note.startTime - timeMs) * layout.pxPerMs;
      if (note.endTime !== null) {
        if (consumed && timeMs >= note.endTime) continue;
        if (consumed) headY = layout.receptorY;
        const tailY = layout.receptorY - (note.endTime - timeMs) * layout.pxPerMs;
        const top = Math.min(headY, tailY);
        const bottom = Math.max(headY, tailY);
        ctx.globalAlpha = missed ? 0.35 : 1;
        ctx.fillStyle = withAlpha(color, 0.42);
        ctx.fillRect(x, top, w, bottom - top);
        ctx.fillStyle = color;
        roundRect(ctx, x, headY - layout.noteH / 2, w, layout.noteH, 5);
        ctx.fill();
        roundRect(ctx, x, tailY - layout.noteH / 2, w, layout.noteH, 5);
        ctx.fill();
      } else {
        if (consumed) continue;
        ctx.globalAlpha = missed ? 0.35 : 1;
        ctx.fillStyle = color;
        roundRect(ctx, x, headY - layout.noteH / 2, w, layout.noteH, 5);
        ctx.fill();
      }
    }
  }
  ctx.globalAlpha = 1;
}

function drawReceptor(ctx: Ctx2D, layout: Layout, keyCount: number): void {
  const pad = Math.max(2, Math.round(layout.colWidth * 0.08));
  ctx.strokeStyle = 'rgba(148, 163, 184, 0.5)';
  ctx.lineWidth = 2;
  for (let c = 0; c < keyCount; c += 1) {
    const x = layout.fieldLeft + c * layout.colWidth + pad;
    roundRect(ctx, x, layout.receptorY - layout.noteH * 0.7, layout.colWidth - pad * 2, layout.noteH * 1.4, 6);
    ctx.stroke();
  }
}

function drawHud(
  ctx: Ctx2D,
  width: number,
  height: number,
  scene: RenderScene,
  timeMs: number,
  layout: Layout,
): void {
  const scale = height / 720;
  ctx.textBaseline = 'alphabetic';

  ctx.fillStyle = '#e2e8f0';
  ctx.textAlign = 'left';
  ctx.font = `${Math.round(22 * scale)}px system-ui, sans-serif`;
  ctx.fillText(scene.chart.title, 20 * scale, 34 * scale);
  ctx.fillStyle = '#94a3b8';
  ctx.font = `${Math.round(14 * scale)}px system-ui, sans-serif`;
  const subtitle = [scene.chart.artist, `[${scene.chart.version}]`, `${scene.chart.keyCount}K`]
    .filter(Boolean)
    .join('  ·  ');
  ctx.fillText(subtitle, 20 * scale, 54 * scale);

  const sample = scene.sim ? sampleSim(scene.sim, timeMs) : null;
  if (scene.replay) {
    ctx.textAlign = 'right';
    ctx.fillStyle = '#f8fafc';
    ctx.font = `${Math.round(30 * scale)}px system-ui, sans-serif`;
    ctx.fillText(String(sample?.score ?? 0).padStart(8, '0'), width - 20 * scale, 40 * scale);
    ctx.fillStyle = '#5eead4';
    ctx.font = `${Math.round(20 * scale)}px system-ui, sans-serif`;
    ctx.fillText(`${((sample?.accuracy ?? 1) * 100).toFixed(2)}%`, width - 20 * scale, 66 * scale);
    if (scene.replay.stats.playerName) {
      ctx.fillStyle = '#94a3b8';
      ctx.font = `${Math.round(13 * scale)}px system-ui, sans-serif`;
      ctx.fillText(scene.replay.stats.playerName, width - 20 * scale, 86 * scale);
    }
  }

  if (sample && sample.combo > 0) {
    ctx.textAlign = 'center';
    ctx.fillStyle = '#f8fafc';
    ctx.font = `${Math.round(46 * scale)}px system-ui, sans-serif`;
    ctx.fillText(String(sample.combo), layout.fieldLeft + layout.fieldWidth / 2, height * 0.42);
  }

  if (sample && sample.judgement && sample.judgementAge >= 0 && sample.judgementAge < 400) {
    const alpha = 1 - sample.judgementAge / 400;
    const centerX = layout.fieldLeft + layout.fieldWidth / 2;
    ctx.textAlign = 'center';
    ctx.globalAlpha = alpha;
    if (sample.judgement === 'max') {
      const half = 52 * scale;
      const gradient = ctx.createLinearGradient(centerX - half, 0, centerX + half, 0);
      MAX_RAINBOW.forEach((color, idx) => gradient.addColorStop(idx / (MAX_RAINBOW.length - 1), color));
      ctx.fillStyle = gradient;
    } else {
      ctx.fillStyle = JUDGEMENT_COLOR[sample.judgement];
    }
    ctx.font = `bold ${Math.round(32 * scale)}px system-ui, sans-serif`;
    ctx.fillText(
      JUDGEMENT_TEXT[sample.judgement],
      centerX,
      height * 0.52 - sample.judgementAge * 0.02 * scale,
    );
    ctx.globalAlpha = 1;
  }
  ctx.textAlign = 'left';
}

function withAlpha(hex: string, alpha: number): string {
  const value = hex.replace('#', '');
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}
