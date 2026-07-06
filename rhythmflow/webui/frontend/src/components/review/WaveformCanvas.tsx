import { useCallback, useEffect, useRef } from "react";
import { t } from "../../i18n";
import type { Language, ReviewSegment, WaveformData, WaveformTrack } from "../../types";
import { adjustedSpans } from "./segmentMath";

interface Rect {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface Geometry {
  plot: Rect;
  reference: Rect;
  video: Rect;
}

function geometry(width: number, height: number): Geometry {
  const left = 86;
  const rightMargin = 18;
  const plot: Rect = {
    left,
    top: 28,
    width: Math.max(1, width - left - rightMargin),
    height: Math.max(1, height - 56),
  };
  const gap = 20;
  const trackHeight = (plot.height - gap) / 2;
  const reference: Rect = {
    left: plot.left,
    top: plot.top,
    width: plot.width,
    height: trackHeight,
  };
  const video: Rect = {
    left: plot.left,
    top: reference.top + trackHeight + gap,
    width: plot.width,
    height: trackHeight,
  };
  return { plot, reference, video };
}

export function WaveformCanvas({
  data,
  segment,
  delta,
  bounds,
  language,
  onAdjust,
}: {
  data: WaveformData;
  segment: ReviewSegment;
  delta: number;
  bounds: { lower: number; upper: number };
  language: Language;
  onAdjust: (delta: number) => void;
}): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const dragRef = useRef<{ startX: number; startDelta: number } | null>(null);

  const clamp = useCallback(
    (value: number) => Math.min(bounds.upper, Math.max(bounds.lower, value)),
    [bounds.lower, bounds.upper],
  );

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(0, 0, width, height);

    const geo = geometry(width, height);
    const [refStart, , videoStart] = adjustedSpans(segment, delta);

    drawTrack(ctx, geo.reference, t(language, "review_wave_reference"));
    drawTrack(ctx, geo.video, t(language, "review_wave_video"));
    drawTicks(ctx, geo.plot, height, data.duration_s);

    drawEnvelope(ctx, geo.reference, data.reference, "#38bdf8", refStart, data.duration_s);
    drawEnvelope(ctx, geo.video, data.video, "#f472b6", videoStart, data.duration_s);

    ctx.fillStyle = "#f8fafc";
    ctx.font = "12px system-ui";
    ctx.textAlign = "right";
    ctx.fillText(
      `${t(language, "review_adjustment")}: ${delta >= 0 ? "+" : ""}${delta.toFixed(3)}s`,
      width - 20,
      18,
    );
    ctx.textAlign = "left";
  }, [data, segment, delta, language]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const handleResize = () => draw();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [draw]);

  const inVideoRect = (offsetX: number, offsetY: number): boolean => {
    const canvas = canvasRef.current;
    if (!canvas) return false;
    const { video } = geometry(canvas.clientWidth, canvas.clientHeight);
    return (
      offsetX >= video.left &&
      offsetX <= video.left + video.width &&
      offsetY >= video.top &&
      offsetY <= video.top + video.height
    );
  };

  const onMouseDown = (event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!inVideoRect(event.nativeEvent.offsetX, event.nativeEvent.offsetY)) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    dragRef.current = { startX: event.nativeEvent.offsetX, startDelta: delta };
    canvas.classList.add("dragging");

    const rectLeft = canvas.getBoundingClientRect().left;
    const secondsPerPixel =
      data.duration_s / geometry(canvas.clientWidth, canvas.clientHeight).plot.width;

    const onMove = (moveEvent: MouseEvent) => {
      if (!dragRef.current) return;
      const currentX = moveEvent.clientX - rectLeft;
      const dx = currentX - dragRef.current.startX;
      onAdjust(clamp(dragRef.current.startDelta - dx * secondsPerPixel));
    };
    const onUp = () => {
      dragRef.current = null;
      canvas.classList.remove("dragging");
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  return (
    <canvas
      ref={canvasRef}
      className="waveform-canvas"
      title={t(language, "review_waveform_tooltip")}
      onMouseDown={onMouseDown}
    />
  );
}

function drawTrack(ctx: CanvasRenderingContext2D, rect: Rect, label: string): void {
  ctx.fillStyle = "#111827";
  ctx.fillRect(rect.left, rect.top, rect.width, rect.height);
  ctx.strokeStyle = "#334155";
  ctx.strokeRect(rect.left, rect.top, rect.width, rect.height);
  const centerY = rect.top + rect.height / 2;
  ctx.strokeStyle = "#475569";
  ctx.beginPath();
  ctx.moveTo(rect.left, centerY);
  ctx.lineTo(rect.left + rect.width, centerY);
  ctx.stroke();
  ctx.fillStyle = "#cbd5e1";
  ctx.font = "12px system-ui";
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillText(label, 8, centerY);
  ctx.textBaseline = "alphabetic";
}

function drawTicks(
  ctx: CanvasRenderingContext2D,
  plot: Rect,
  height: number,
  duration: number,
): void {
  for (const ratio of [0, 0.5, 1]) {
    const x = plot.left + plot.width * ratio;
    ctx.strokeStyle = "#475569";
    ctx.beginPath();
    ctx.moveTo(x, plot.top);
    ctx.lineTo(x, plot.top + plot.height);
    ctx.stroke();
    ctx.fillStyle = "#94a3b8";
    ctx.font = "11px system-ui";
    ctx.textAlign = "center";
    ctx.fillText(`${(duration * ratio).toFixed(1)}s`, x, height - 8);
    ctx.textAlign = "left";
  }
}

function drawEnvelope(
  ctx: CanvasRenderingContext2D,
  rect: Rect,
  track: WaveformTrack,
  color: string,
  alignedStart: number,
  duration: number,
): void {
  const envelope = track.envelope;
  const points = envelope.length;
  if (points === 0 || track.window_duration_s <= 0) return;
  const amplitude = rect.height * 0.42;
  const centerY = rect.top + rect.height / 2;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < points; i += 1) {
    const localTime = ((i + 0.5) / points) * track.window_duration_s;
    const absoluteTime = track.window_start_s + localTime;
    const x = rect.left + ((absoluteTime - alignedStart) / duration) * rect.width;
    if (x < rect.left - 1 || x > rect.left + rect.width + 1) continue;
    const half = envelope[i] * amplitude;
    ctx.moveTo(x, centerY - half);
    ctx.lineTo(x, centerY + half);
  }
  ctx.stroke();
}
