import type {
  AboutInfo,
  AppContext,
  CommandResult,
  ReferenceGame,
  ReferenceSong,
  ReviewDelta,
  ReviewSegment,
  RowState,
  Settings,
  WaveformData,
} from './types';

export interface RhythmApi {
  get_settings(): Promise<Settings>;
  save_settings(values: Partial<Settings>): Promise<Settings>;
  about_info(): Promise<AboutInfo>;
  open_repository(): Promise<void>;
  get_media_base(): Promise<string>;
  register_media(path: string): Promise<string>;
  pick_videos(): Promise<string[]>;
  pick_reference(): Promise<string | null>;
  search_reference_songs(game: ReferenceGame, query: string): Promise<ReferenceSong[]>;
  download_reference_audio(
    game: ReferenceGame,
    assetSongId: string,
    title: string,
    persist: boolean,
  ): Promise<{ ok: boolean; path?: string; error?: string }>;
  pick_output_dir(): Promise<string | null>;
  begin_osu_export(filename: string): Promise<{ ok: boolean; token?: string; output_path?: string; error?: string }>;
  append_osu_export_chunk(token: string, chunkBase64: string): Promise<{ ok: boolean; bytes?: number; error?: string }>;
  finish_osu_export(token: string): Promise<{ ok: boolean; output_path?: string; bytes?: number; error?: string }>;
  abort_osu_export(token: string): Promise<{ ok: boolean; error?: string }>;
  sync_rows(paths: string[], context: AppContext): Promise<RowState[]>;
  set_nudge(row: number, value: number): Promise<RowState | null>;
  get_rows(): Promise<RowState[]>;
  analyze(videos: string[], reference: string, context: AppContext): Promise<CommandResult>;
  get_review_segments(): Promise<{ segments: ReviewSegment[] }>;
  get_waveform(segment: ReviewSegment): Promise<WaveformData>;
  apply_review(deltas: ReviewDelta[]): Promise<RowState[]>;
  process(context: AppContext): Promise<CommandResult>;
}

type Listener = (payload: any) => void;

const listeners = new Map<string, Set<Listener>>();

function dispatch(message: { event: string; payload: unknown }): void {
  const set = listeners.get(message.event);
  if (set) {
    for (const listener of set) {
      try {
        listener(message.payload);
      } catch (err) {
        console.error('rhythmflow event listener failed', err);
      }
    }
  }
}

// Python pushes events through this global (see webui/events.py).
(window as any).rhythmflowBridge = { dispatch };

export function onEvent(event: string, listener: Listener): () => void {
  let set = listeners.get(event);
  if (!set) {
    set = new Set();
    listeners.set(event, set);
  }
  set.add(listener);
  return () => set?.delete(listener);
}

let cached: Promise<RhythmApi> | null = null;

// pywebview injects `window.pywebview.api` as an empty stub first, then
// populates it (a different object) once the bridge is ready. Resolve with a
// live proxy that always forwards to the *current* api object, and only once a
// real method exists — otherwise we capture the empty stub and every call
// throws "is not a function".
function apiReady(): boolean {
  return typeof (window as any).pywebview?.api?.get_settings === 'function';
}

function liveApi(): RhythmApi {
  return new Proxy(
    {},
    {
      get(_target, prop) {
        // Never expose `then` (or symbols) or the promise machinery treats this
        // proxy as a thenable and tries to call window.pywebview.api.then().
        if (prop === 'then' || typeof prop === 'symbol') return undefined;
        return (...args: unknown[]) => (window as any).pywebview.api[prop as string](...args);
      },
    },
  ) as RhythmApi;
}

export function getApi(): Promise<RhythmApi> {
  if (cached) return cached;
  cached = new Promise((resolve) => {
    if (apiReady()) {
      resolve(liveApi());
      return;
    }
    const start = Date.now();
    const timer = window.setInterval(() => {
      if (apiReady()) {
        window.clearInterval(timer);
        resolve(liveApi());
      } else if (!(window as any).pywebview && Date.now() - start > 1200) {
        // Plain browser (dev / preview) — fall back to the mock bridge.
        window.clearInterval(timer);
        console.info('pywebview not detected — using mock bridge');
        resolve(createMockApi(dispatch));
      }
    }, 40);
    window.addEventListener(
      'pywebviewready',
      () => {
        if (apiReady()) {
          window.clearInterval(timer);
          resolve(liveApi());
        }
      },
      { once: true },
    );
  });
  return cached;
}

// ---- Mock bridge (browser preview only) --------------------------------
function createMockApi(emit: (m: { event: string; payload: unknown }) => void): RhythmApi {
  let rows: RowState[] = [];
  const settings: Settings = {
    language: 'zh',
    output_dir: 'E:/Code/RhythmFlow/output',
    output_pattern: '{name}_aligned.mp4',
    original_volume: 15,
    reference_volume: 100,
    cut_mode: 'accurate',
  };
  const makeRow = (path: string): RowState => ({
    video_path: path,
    file_name: path.split(/[\\/]/).pop() ?? path,
    analyzed: false,
    error: null,
    detected_offset: null,
    confidence: null,
    nudge: 0,
    final_offset: null,
    smart_trim_s: 0,
    smart_trim_count: 0,
    smart_confidence: null,
    needs_review: false,
    review_confirmed: false,
    warnings: [],
  });
  let counter = 0;
  const osuExports = new Map<string, { filename: string; chunks: BlobPart[] }>();
  return {
    async get_settings() {
      return settings;
    },
    async save_settings(values) {
      Object.assign(settings, values);
      return settings;
    },
    async about_info() {
      return {
        app_name: 'RhythmFlow',
        version: '0.2.1',
        author: 'HaoduStudio',
        repository: 'https://github.com/HaoduStudio/RhythmFlow',
      };
    },
    async open_repository() {},
    async get_media_base() {
      return '';
    },
    async register_media(path) {
      return path;
    },
    async pick_videos() {
      counter += 1;
      return [`E:/samples/handcam_${counter}.mp4`];
    },
    async pick_reference() {
      return 'E:/samples/reference.wav';
    },
    async search_reference_songs(game, query) {
      const songs: ReferenceSong[] =
        game === 'maimai'
          ? [
              {
                id: '1001',
                title: 'Stellar Parade',
                artist: 'Sample Artist',
                version: 'BUDDiES',
                genre: 'POPS',
                difficulty_summary: 'MASTER 13 / Re:MASTER 14',
                difficulties: [
                  { label: 'MASTER', level: '13', index: 3 },
                  { label: 'Re:MASTER', level: '14', index: 4 },
                ],
                asset_song_id: '1001',
              },
              {
                id: '1002',
                title: 'Blue Mirage',
                artist: 'RhythmFlow',
                version: 'Festival',
                genre: 'Game',
                difficulty_summary: 'EXPERT 11+ / MASTER 13+',
                difficulties: [
                  { label: 'BASIC', level: '4', index: 0 },
                  { label: 'ADVANCED', level: '8', index: 1 },
                  { label: 'EXPERT', level: '11+', index: 2 },
                  { label: 'MASTER', level: '13+', index: 3 },
                ],
                asset_song_id: '1002',
              },
            ]
          : [
              {
                id: '3001',
                title: "World's End Song",
                artist: 'Chuni Artist',
                version: 'SUN',
                genre: 'VARIETY',
                difficulty_summary: "WORLD'S END 避",
                difficulties: [{ label: "WORLD'S END", level: '避', index: 5 }],
                asset_song_id: '8888',
              },
              {
                id: '3002',
                title: 'Luminous Rail',
                artist: 'Sample Artist',
                version: 'VERSE',
                genre: 'ORIGINAL',
                difficulty_summary: 'MASTER 13',
                difficulties: [
                  { label: 'MASTER', level: '13', index: 3 },
                  { label: 'ULTIMA', level: '14+', index: 4 },
                ],
                asset_song_id: '3002',
              },
            ];
      const needle = query.trim().toLowerCase();
      if (!needle) return songs;
      return songs.filter((song) =>
        [song.id, song.title, song.artist, song.version, song.genre, song.difficulty_summary]
          .join(' ')
          .toLowerCase()
          .includes(needle),
      );
    },
    async download_reference_audio(game, assetSongId, title, persist) {
      const root = persist ? settings.output_dir : 'E:/Code/RhythmFlow/cache';
      const safeTitle = title.replace(/[<>:"/\\|?*]/g, '_');
      return { ok: true, path: `${root}/${game}_${assetSongId}_${safeTitle}.mp3` };
    },
    async pick_output_dir() {
      return 'E:/Code/RhythmFlow/output';
    },
    async begin_osu_export(filename) {
      counter += 1;
      const token = `mock-osu-${counter}`;
      osuExports.set(token, { filename, chunks: [] });
      return { ok: true, token, output_path: `${settings.output_dir}/${filename}` };
    },
    async append_osu_export_chunk(token, chunkBase64) {
      const entry = osuExports.get(token);
      if (!entry) return { ok: false, error: 'invalid_export_session' };
      const binary = atob(chunkBase64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      entry.chunks.push(bytes);
      return { ok: true };
    },
    async finish_osu_export(token) {
      const entry = osuExports.get(token);
      osuExports.delete(token);
      if (!entry) return { ok: false, error: 'invalid_export_session' };
      // Browser dev cannot write to disk — hand the file over as a download instead.
      const type = entry.filename.toLowerCase().endsWith('.webm') ? 'video/webm' : 'video/mp4';
      const blob = new Blob(entry.chunks, { type });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = entry.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      return { ok: true, output_path: `${settings.output_dir}/${entry.filename}`, bytes: blob.size };
    },
    async abort_osu_export(token) {
      osuExports.delete(token);
      return { ok: true };
    },
    async sync_rows(paths) {
      rows = paths.map(makeRow);
      return rows;
    },
    async set_nudge(row, value) {
      if (rows[row]) {
        rows[row].nudge = value;
        rows[row].final_offset = (rows[row].detected_offset ?? 0) + value;
        return rows[row];
      }
      return null;
    },
    async get_rows() {
      return rows;
    },
    async analyze(videos) {
      window.setTimeout(() => {
        videos.forEach((_v, index) => {
          const needsReview = index % 2 === 1;
          const row: RowState = {
            ...makeRow(rows[index]?.video_path ?? videos[index]),
            analyzed: true,
            detected_offset: 1.234 + index,
            confidence: needsReview ? 1.1 : 2.6,
            final_offset: 1.234 + index,
            smart_trim_s: needsReview ? 2.5 : 0,
            smart_trim_count: needsReview ? 1 : 0,
            smart_confidence: needsReview ? 0.55 : null,
            needs_review: needsReview,
            review_confirmed: !needsReview,
          };
          rows[index] = row;
          emit({ event: 'analyze_result', payload: { row: index, row_state: row } });
        });
        emit({ event: 'progress', payload: 100 });
        emit({
          event: 'analyze_finished',
          payload: { rows, review_rows: rows.map((r, i) => (r.needs_review ? i : -1)).filter((i) => i >= 0) },
        });
      }, 400);
      return { ok: true };
    },
    async get_review_segments() {
      const reviewRows = rows.map((r, i) => (r.needs_review ? i : -1)).filter((i) => i >= 0);
      return {
        segments: reviewRows.map((row) => ({
          id: `${row}:0`,
          row,
          segment_index: 0,
          is_global: true,
          file_name: rows[row].file_name,
          video_path: rows[row].video_path,
          reference_path: 'E:/samples/reference.wav',
          video_url: '',
          reference_url: '',
          label_key: 'review_global_label',
          label_params: {},
          notes: [{ key: 'review_low_confidence_note', params: { confidence: '1.10' } }],
          reference_start_s: 0,
          reference_end_s: 8,
          video_start_s: 1.234,
          video_end_s: 9.234,
        })),
      };
    },
    async get_waveform() {
      const envelope = Array.from({ length: 900 }, (_v, i) => Math.abs(Math.sin(i / 12)) * (0.4 + Math.random() * 0.6));
      return {
        ok: true,
        duration_s: 8,
        bounds: { lower: -2, upper: 2 },
        reference: { envelope, window_start_s: 0, window_duration_s: 12 },
        video: { envelope: [...envelope].reverse(), window_start_s: 0, window_duration_s: 12 },
      };
    },
    async apply_review() {
      rows = rows.map((r) => (r.needs_review ? { ...r, review_confirmed: true } : r));
      return rows;
    },
    async process() {
      window.setTimeout(() => {
        emit({ event: 'progress', payload: 100 });
        emit({ event: 'process_finished', payload: { rows } });
      }, 400);
      return { ok: true };
    },
  };
}
