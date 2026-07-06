import { ArrayBufferTarget as Mp4Target, Muxer as Mp4Muxer } from "mp4-muxer";
import { ArrayBufferTarget as WebMTarget, Muxer as WebMMuxer } from "webm-muxer";

type Ctx2D = OffscreenCanvasRenderingContext2D;

export interface ExportParams {
  width: number;
  height: number;
  fps: number;
  container: "mp4" | "webm";
  durationMs: number;
  audioBuffer: AudioBuffer | null;
  renderFrame: (ctx: Ctx2D, timeMs: number) => void;
  onProgress?: (done: number, total: number) => void;
  signal?: AbortSignal;
}

export interface ExportResult {
  blob: Blob;
  container: "mp4" | "webm";
}

interface ChosenConfig {
  container: "mp4" | "webm";
  videoCodec: string;
  muxVideoCodec: "avc" | "vp9";
  audioCodec: string | null;
  videoBitrate: number;
}

export function webCodecsAvailable(): boolean {
  return typeof VideoEncoder !== "undefined" && typeof VideoFrame !== "undefined";
}

async function videoSupported(
  codec: string,
  width: number,
  height: number,
  fps: number,
  bitrate: number,
): Promise<boolean> {
  try {
    const support = await VideoEncoder.isConfigSupported({
      codec,
      width,
      height,
      framerate: fps,
      bitrate,
    });
    return Boolean(support.supported);
  } catch {
    return false;
  }
}

async function audioSupported(
  codec: string,
  sampleRate: number,
  channels: number,
): Promise<boolean> {
  try {
    const support = await AudioEncoder.isConfigSupported({
      codec,
      sampleRate,
      numberOfChannels: channels,
      bitrate: 192000,
    });
    return Boolean(support.supported);
  } catch {
    return false;
  }
}

async function chooseConfig(params: ExportParams): Promise<ChosenConfig | null> {
  const { width, height, fps, container } = params;
  const videoBitrate = Math.min(24_000_000, Math.round(width * height * fps * 0.07));
  const channels = params.audioBuffer?.numberOfChannels ?? 2;
  const sampleRate = params.audioBuffer?.sampleRate ?? 48000;

  const avc = ["avc1.640028", "avc1.4d0028", "avc1.42e01e"];
  const wantMp4 = container === "mp4";

  const aacOk = params.audioBuffer
    ? await audioSupported("mp4a.40.2", sampleRate, channels)
    : false;
  const opusOk = params.audioBuffer ? await audioSupported("opus", sampleRate, channels) : false;

  if (wantMp4) {
    for (const codec of avc) {
      if (await videoSupported(codec, width, height, fps, videoBitrate)) {
        return {
          container: "mp4",
          videoCodec: codec,
          muxVideoCodec: "avc",
          audioCodec: aacOk ? "mp4a.40.2" : null,
          videoBitrate,
        };
      }
    }
  }
  if (await videoSupported("vp09.00.10.08", width, height, fps, videoBitrate)) {
    return {
      container: "webm",
      videoCodec: "vp09.00.10.08",
      muxVideoCodec: "vp9",
      audioCodec: opusOk ? "opus" : null,
      videoBitrate,
    };
  }
  for (const codec of avc) {
    if (await videoSupported(codec, width, height, fps, videoBitrate)) {
      return {
        container: "mp4",
        videoCodec: codec,
        muxVideoCodec: "avc",
        audioCodec: aacOk ? "mp4a.40.2" : null,
        videoBitrate,
      };
    }
  }
  return null;
}

interface UnifiedMuxer {
  addVideoChunk(chunk: EncodedVideoChunk, meta?: EncodedVideoChunkMetadata): void;
  addAudioChunk(chunk: EncodedAudioChunk, meta?: EncodedAudioChunkMetadata): void;
  finalize(): ArrayBuffer;
}

function buildMuxer(config: ChosenConfig, params: ExportParams): UnifiedMuxer {
  const { width, height, fps, audioBuffer } = params;
  const channels = audioBuffer?.numberOfChannels ?? 2;
  const sampleRate = audioBuffer?.sampleRate ?? 48000;

  if (config.container === "mp4") {
    const target = new Mp4Target();
    const muxer = new Mp4Muxer({
      target,
      fastStart: "in-memory",
      video: { codec: config.muxVideoCodec, width, height, frameRate: fps },
      audio: config.audioCodec
        ? { codec: "aac", numberOfChannels: channels, sampleRate }
        : undefined,
      firstTimestampBehavior: "offset",
    });
    return {
      addVideoChunk: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
      addAudioChunk: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
      finalize: () => {
        muxer.finalize();
        return target.buffer;
      },
    };
  }

  const target = new WebMTarget();
  const muxer = new WebMMuxer({
    target,
    video: { codec: "V_VP9", width, height, frameRate: fps },
    audio: config.audioCodec
      ? { codec: "A_OPUS", numberOfChannels: channels, sampleRate }
      : undefined,
    firstTimestampBehavior: "offset",
  });
  return {
    addVideoChunk: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    addAudioChunk: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
    finalize: () => {
      muxer.finalize();
      return target.buffer;
    },
  };
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("Export cancelled", "AbortError");
}

async function drainQueue(encoder: VideoEncoder, limit: number): Promise<void> {
  while (encoder.encodeQueueSize > limit) {
    await new Promise((resolve) => setTimeout(resolve, 4));
  }
}

async function encodeAudio(
  muxer: UnifiedMuxer,
  config: ChosenConfig,
  buffer: AudioBuffer,
  durationMs: number,
  signal?: AbortSignal,
): Promise<void> {
  if (!config.audioCodec) return;
  const encoder = new AudioEncoder({
    output: (chunk, meta) => muxer.addAudioChunk(chunk, meta),
    error: (err) => console.error("audio encode error", err),
  });
  encoder.configure({
    codec: config.audioCodec,
    sampleRate: buffer.sampleRate,
    numberOfChannels: buffer.numberOfChannels,
    bitrate: 192000,
  });

  const channels = buffer.numberOfChannels;
  const totalFrames = Math.min(buffer.length, Math.ceil((durationMs / 1000) * buffer.sampleRate));
  const chunkFrames = Math.round(buffer.sampleRate * 0.1);
  const channelData = Array.from({ length: channels }, (_, c) => buffer.getChannelData(c));

  for (let start = 0; start < totalFrames; start += chunkFrames) {
    throwIfAborted(signal);
    const count = Math.min(chunkFrames, totalFrames - start);
    const planar = new Float32Array(count * channels);
    for (let c = 0; c < channels; c += 1) {
      planar.set(channelData[c].subarray(start, start + count), c * count);
    }
    const data = new AudioData({
      format: "f32-planar",
      sampleRate: buffer.sampleRate,
      numberOfFrames: count,
      numberOfChannels: channels,
      timestamp: Math.round((start / buffer.sampleRate) * 1_000_000),
      data: planar,
    });
    encoder.encode(data);
    data.close();
  }
  await encoder.flush();
  encoder.close();
}

export async function exportVideo(params: ExportParams): Promise<ExportResult> {
  if (!webCodecsAvailable()) {
    throw new Error("webcodecs_unavailable");
  }
  const config = await chooseConfig(params);
  if (!config) {
    throw new Error("codec_unavailable");
  }

  const muxer = buildMuxer(config, params);
  const canvas = new OffscreenCanvas(params.width, params.height);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("canvas_unavailable");

  const videoEncoder = new VideoEncoder({
    output: (chunk, meta) => muxer.addVideoChunk(chunk, meta),
    error: (err) => console.error("video encode error", err),
  });
  videoEncoder.configure({
    codec: config.videoCodec,
    width: params.width,
    height: params.height,
    framerate: params.fps,
    bitrate: config.videoBitrate,
  });

  if (params.audioBuffer) {
    await encodeAudio(muxer, config, params.audioBuffer, params.durationMs, params.signal);
  }

  const totalFrames = Math.max(1, Math.ceil((params.durationMs / 1000) * params.fps));
  const frameDuration = Math.round(1_000_000 / params.fps);
  const keyInterval = params.fps * 2;

  for (let frame = 0; frame < totalFrames; frame += 1) {
    throwIfAborted(params.signal);
    const timeMs = (frame / params.fps) * 1000;
    params.renderFrame(ctx, timeMs);
    const videoFrame = new VideoFrame(canvas, {
      timestamp: Math.round((frame * 1_000_000) / params.fps),
      duration: frameDuration,
    });
    videoEncoder.encode(videoFrame, { keyFrame: frame % keyInterval === 0 });
    videoFrame.close();
    if (videoEncoder.encodeQueueSize > 8) await drainQueue(videoEncoder, 4);
    params.onProgress?.(frame + 1, totalFrames);
  }

  await videoEncoder.flush();
  videoEncoder.close();
  const buffer = muxer.finalize();

  return {
    blob: new Blob([buffer], { type: config.container === "mp4" ? "video/mp4" : "video/webm" }),
    container: config.container,
  };
}

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}
