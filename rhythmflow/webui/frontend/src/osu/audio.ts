export class OsuAudioPlayer {
  private ctx: AudioContext;
  private gain: GainNode;
  private buffer: AudioBuffer | null = null;
  private source: AudioBufferSourceNode | null = null;
  private startedAtCtx = 0;
  private startOffsetMs = 0;
  private playingState = false;
  private rate = 1;

  constructor() {
    this.ctx = new AudioContext();
    this.gain = this.ctx.createGain();
    this.gain.connect(this.ctx.destination);
  }

  get playing(): boolean {
    return this.playingState;
  }

  get durationMs(): number {
    return this.buffer ? this.buffer.duration * 1000 : 0;
  }

  get audioBuffer(): AudioBuffer | null {
    return this.buffer;
  }

  async load(data: ArrayBuffer | Uint8Array): Promise<void> {
    const bytes =
      data instanceof Uint8Array
        ? data.slice().buffer
        : data.slice(0);
    this.buffer = await this.ctx.decodeAudioData(bytes as ArrayBuffer);
    this.startOffsetMs = 0;
  }

  setRate(rate: number): void {
    this.rate = rate;
    if (this.source) this.source.playbackRate.value = rate;
  }

  setVolume(volume: number): void {
    this.gain.gain.value = volume;
  }

  currentTimeMs(): number {
    if (this.playingState) {
      return this.startOffsetMs + (this.ctx.currentTime - this.startedAtCtx) * 1000 * this.rate;
    }
    return this.startOffsetMs;
  }

  play(): void {
    if (!this.buffer || this.playingState) return;
    if (this.ctx.state === 'suspended') void this.ctx.resume();
    const source = this.ctx.createBufferSource();
    source.buffer = this.buffer;
    source.playbackRate.value = this.rate;
    source.connect(this.gain);
    source.start(0, Math.max(0, this.startOffsetMs / 1000));
    this.startedAtCtx = this.ctx.currentTime;
    this.source = source;
    this.playingState = true;
  }

  pause(): void {
    if (!this.playingState) return;
    this.startOffsetMs = Math.min(this.currentTimeMs(), this.durationMs);
    this.stopSource();
    this.playingState = false;
  }

  seek(ms: number): void {
    const wasPlaying = this.playingState;
    if (wasPlaying) {
      this.stopSource();
      this.playingState = false;
    }
    this.startOffsetMs = Math.max(0, Math.min(ms, this.durationMs));
    if (wasPlaying) this.play();
  }

  private stopSource(): void {
    if (!this.source) return;
    try {
      this.source.stop();
    } catch {
      // Source may already be stopped.
    }
    this.source.disconnect();
    this.source = null;
  }

  dispose(): void {
    this.stopSource();
    void this.ctx.close();
  }
}

export async function decodeAudioBuffer(data: Uint8Array): Promise<AudioBuffer> {
  const ctx = new AudioContext();
  try {
    return await ctx.decodeAudioData(data.slice().buffer as ArrayBuffer);
  } finally {
    void ctx.close();
  }
}
