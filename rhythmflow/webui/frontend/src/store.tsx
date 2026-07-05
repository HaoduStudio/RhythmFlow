import { App } from 'antd';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  type ReactNode,
} from 'react';
import { getApi, onEvent, type RhythmApi } from './bridge';
import { t } from './i18n';
import type {
  AppContext as ApiContext,
  AppPage,
  Language,
  ReferenceGame,
  ReferenceSong,
  ReviewDelta,
  ReviewSegment,
  RowState,
  Settings,
} from './types';

interface State {
  ready: boolean;
  page: AppPage;
  settings: Settings;
  reference: string;
  rows: RowState[];
  log: string[];
  progress: number;
  busy: boolean;
  reviewOpen: boolean;
  reviewSegments: ReviewSegment[];
  aboutOpen: boolean;
}

const DEFAULT_SETTINGS: Settings = {
  language: 'zh',
  output_dir: '',
  output_pattern: '{name}_aligned.mp4',
  original_volume: 15,
  reference_volume: 100,
  cut_mode: 'accurate',
};

const initialState: State = {
  ready: false,
  page: 'smart',
  settings: DEFAULT_SETTINGS,
  reference: '',
  rows: [],
  log: [],
  progress: 0,
  busy: false,
  reviewOpen: false,
  reviewSegments: [],
  aboutOpen: false,
};

type Action =
  | { type: 'ready'; settings: Settings }
  | { type: 'page'; page: AppPage }
  | { type: 'settings'; settings: Settings }
  | { type: 'reference'; reference: string }
  | { type: 'rows'; rows: RowState[] }
  | { type: 'row'; row: number; rowState: RowState }
  | { type: 'log'; line: string }
  | { type: 'progress'; value: number }
  | { type: 'busy'; value: boolean }
  | { type: 'review'; open: boolean; segments?: ReviewSegment[] }
  | { type: 'about'; open: boolean };

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case 'ready':
      return { ...state, ready: true, settings: action.settings };
    case 'page':
      return { ...state, page: action.page };
    case 'settings':
      return { ...state, settings: action.settings };
    case 'reference':
      return { ...state, reference: action.reference };
    case 'rows':
      return { ...state, rows: action.rows };
    case 'row': {
      const rows = state.rows.slice();
      if (action.row >= 0 && action.row < rows.length) {
        rows[action.row] = action.rowState;
      }
      return { ...state, rows };
    }
    case 'log':
      return { ...state, log: [...state.log, action.line].slice(-500) };
    case 'progress':
      return { ...state, progress: action.value };
    case 'busy':
      return { ...state, busy: action.value };
    case 'review':
      return {
        ...state,
        reviewOpen: action.open,
        reviewSegments: action.segments ?? state.reviewSegments,
      };
    case 'about':
      return { ...state, aboutOpen: action.open };
    default:
      return state;
  }
}

export interface StoreValue extends State {
  language: Language;
  setPage: (page: AppPage) => void;
  addVideos: () => Promise<void>;
  removeVideos: (paths: string[]) => Promise<void>;
  clearVideos: () => Promise<void>;
  pickReference: () => Promise<void>;
  pickOutputDir: () => Promise<void>;
  searchReferenceSongs: (game: ReferenceGame, query: string) => Promise<ReferenceSong[]>;
  downloadReferenceAudio: (
    game: ReferenceGame,
    assetSongId: string,
    title: string,
    persist: boolean,
  ) => Promise<string>;
  setReference: (value: string) => void;
  setNudge: (row: number, value: number) => Promise<void>;
  updateSettings: (patch: Partial<Settings>) => void;
  analyze: () => Promise<void>;
  process: () => Promise<void>;
  openReview: () => Promise<void>;
  closeReview: () => void;
  acceptReview: (deltas: ReviewDelta[]) => Promise<void>;
  openAbout: () => void;
  closeAbout: () => void;
  getWaveform: RhythmApi['get_waveform'];
}

const StoreContext = createContext<StoreValue | null>(null);

function buildContext(settings: Settings, reference: string): ApiContext {
  return {
    language: settings.language,
    reference_path: reference,
    output_dir: settings.output_dir,
    output_pattern: settings.output_pattern,
    original_volume: settings.original_volume,
    reference_volume: settings.reference_volume,
    mode: settings.cut_mode,
  };
}

function dedupe(paths: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const path of paths) {
    if (!seen.has(path)) {
      seen.add(path);
      result.push(path);
    }
  }
  return result;
}

export function StoreProvider({ children }: { children: ReactNode }): JSX.Element {
  const [state, dispatch] = useReducer(reducer, initialState);
  const { message } = App.useApp();
  const apiRef = useRef<RhythmApi | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const ctx = useCallback(
    () => buildContext(stateRef.current.settings, stateRef.current.reference),
    [],
  );
  const lang = useCallback(() => stateRef.current.settings.language, []);

  // Load the API + settings, and wire Python -> JS events once.
  useEffect(() => {
    let disposed = false;
    getApi().then(async (api) => {
      if (disposed) return;
      apiRef.current = api;
      const settings = await api.get_settings();
      dispatch({ type: 'ready', settings });
    });

    const offs = [
      onEvent('busy', (value: boolean) => dispatch({ type: 'busy', value })),
      onEvent('progress', (value: number) => dispatch({ type: 'progress', value })),
      onEvent('log', (line: string) => dispatch({ type: 'log', line })),
      onEvent('analyze_result', (p: { row: number; row_state: RowState }) =>
        dispatch({ type: 'row', row: p.row, rowState: p.row_state }),
      ),
      onEvent('analyze_finished', (p: { rows: RowState[]; review_rows: number[] }) => {
        dispatch({ type: 'rows', rows: p.rows });
        if (p.review_rows.length > 0) {
          message.warning(t(lang(), 'review_prompt', { count: p.review_rows.length }));
          void openReviewInternal();
        } else {
          message.success(t(lang(), 'analysis_complete'));
        }
      }),
      onEvent('process_finished', (p: { rows: RowState[] }) => {
        dispatch({ type: 'rows', rows: p.rows });
        message.success(t(lang(), 'processing_complete'));
      }),
      onEvent('error', (msg: string) => {
        dispatch({ type: 'log', line: msg });
        message.error(msg);
      }),
    ];
    return () => {
      disposed = true;
      offs.forEach((off) => off());
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openReviewInternal = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const { segments } = await api.get_review_segments();
    dispatch({ type: 'review', open: true, segments });
  }, []);

  const syncRows = useCallback(
    async (paths: string[]) => {
      const api = apiRef.current;
      if (!api) return;
      const rows = await api.sync_rows(paths, ctx());
      dispatch({ type: 'rows', rows });
    },
    [ctx],
  );

  const addVideos = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const picked = await api.pick_videos();
    if (!picked.length) return;
    const merged = dedupe([...stateRef.current.rows.map((r) => r.video_path), ...picked]);
    await syncRows(merged);
  }, [syncRows]);

  const removeVideos = useCallback(
    async (paths: string[]) => {
      const drop = new Set(paths);
      const remaining = stateRef.current.rows
        .map((r) => r.video_path)
        .filter((p) => !drop.has(p));
      await syncRows(remaining);
    },
    [syncRows],
  );

  const clearVideos = useCallback(async () => {
    await syncRows([]);
  }, [syncRows]);

  const pickReference = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const picked = await api.pick_reference();
    if (picked) dispatch({ type: 'reference', reference: picked });
  }, []);

  const searchReferenceSongs = useCallback(
    async (game: ReferenceGame, query: string) => {
      const api = apiRef.current;
      if (!api) return [];
      return api.search_reference_songs(game, query);
    },
    [],
  );

  const downloadReferenceAudio = useCallback(
    async (game: ReferenceGame, assetSongId: string, title: string, persistReference: boolean) => {
      const api = apiRef.current;
      if (!api) throw new Error('API not ready');
      const result = await api.download_reference_audio(game, assetSongId, title, persistReference);
      if (!result.ok || !result.path) {
        throw new Error(result.error || 'download failed');
      }
      return result.path;
    },
    [],
  );

  const persist = useCallback((patch: Partial<Settings>) => {
    apiRef.current?.save_settings(patch);
  }, []);

  const updateSettings = useCallback(
    (patch: Partial<Settings>) => {
      const settings = { ...stateRef.current.settings, ...patch };
      dispatch({ type: 'settings', settings });
      persist(patch);
    },
    [persist],
  );

  const pickOutputDir = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const picked = await api.pick_output_dir();
    if (picked) updateSettings({ output_dir: picked });
  }, [updateSettings]);

  const setReference = useCallback((value: string) => {
    dispatch({ type: 'reference', reference: value });
  }, []);

  const setNudge = useCallback(async (row: number, value: number) => {
    const api = apiRef.current;
    if (!api) return;
    const updated = await api.set_nudge(row, value);
    if (updated) dispatch({ type: 'row', row, rowState: updated });
  }, []);

  const analyze = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    const current = stateRef.current;
    if (current.rows.length === 0) {
      message.warning(t(lang(), 'warn_add_video'));
      return;
    }
    if (!current.reference) {
      message.warning(t(lang(), 'warn_choose_reference'));
      return;
    }
    dispatch({ type: 'progress', value: 0 });
    const result = await api.analyze(
      current.rows.map((r) => r.video_path),
      current.reference,
      ctx(),
    );
    if (!result.ok && result.error) {
      message.warning(t(lang(), result.error));
    }
  }, [ctx, lang, message]);

  const process = useCallback(async () => {
    const api = apiRef.current;
    if (!api) return;
    dispatch({ type: 'progress', value: 0 });
    const result = await api.process(ctx());
    if (!result.ok && result.error) {
      if (result.error === 'warn_review_required') {
        message.warning(t(lang(), 'warn_review_required'));
        await openReviewInternal();
      } else {
        message.warning(t(lang(), result.error));
      }
    }
  }, [ctx, lang, message, openReviewInternal]);

  const acceptReview = useCallback(async (deltas: ReviewDelta[]) => {
    const api = apiRef.current;
    if (!api) return;
    const rows = await api.apply_review(deltas);
    dispatch({ type: 'rows', rows });
    dispatch({ type: 'review', open: false });
  }, []);

  const getWaveform = useCallback<RhythmApi['get_waveform']>(async (segment) => {
    const api = apiRef.current;
    if (!api) throw new Error('API not ready');
    return api.get_waveform(segment);
  }, []);

  const value = useMemo<StoreValue>(
    () => ({
      ...state,
      language: state.settings.language,
      setPage: (page: AppPage) => dispatch({ type: 'page', page }),
      addVideos,
      removeVideos,
      clearVideos,
      pickReference,
      pickOutputDir,
      searchReferenceSongs,
      downloadReferenceAudio,
      setReference,
      setNudge,
      updateSettings,
      analyze,
      process,
      openReview: openReviewInternal,
      closeReview: () => dispatch({ type: 'review', open: false }),
      acceptReview,
      openAbout: () => dispatch({ type: 'about', open: true }),
      closeAbout: () => dispatch({ type: 'about', open: false }),
      getWaveform,
    }),
    [
      state,
      addVideos,
      removeVideos,
      clearVideos,
      pickReference,
      pickOutputDir,
      searchReferenceSongs,
      downloadReferenceAudio,
      setReference,
      setNudge,
      updateSettings,
      analyze,
      process,
      openReviewInternal,
      acceptReview,
      getWaveform,
    ],
  );

  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

export function useStore(): StoreValue {
  const value = useContext(StoreContext);
  if (!value) throw new Error('useStore must be used within StoreProvider');
  return value;
}
