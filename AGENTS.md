# RhythmFlow — AI Agent Guide

## Project Overview

RhythmFlow is a cross-platform (Windows/macOS) desktop application for aligning hand-cam ("hand-cam") arcade rhythm game videos to clean reference audio. It estimates global offsets via chroma-feature cross-correlation, supports smart segmented alignment to detect extra sections, and exports aligned MP4s with a configurable audio blend.

- **Entry point**: `rhythmflow/app.py` → `python -m rhythmflow`
- **Package name**: `rhythmflow` (version 0.2.2)
- **License**: Apache 2.0

## Tech Stack

| Layer       | Technology                                    |
| ----------- | --------------------------------------------- |
| Backend     | Python >= 3.11                                |
| Desktop GUI | pywebview >= 5.0                              |
| Frontend    | React 18 + TypeScript + Ant Design 5 + Vite 5 |
| Audio DSP   | numpy, scipy, librosa                         |
| Audio I/O   | pydub, imageio-ffmpeg                         |
| Packaging   | PyInstaller                                   |
| CI/CD       | GitHub Actions (Windows + macOS)              |
| Testing     | Python `unittest` (stdlib)                    |

## Commands

### Setup

```powershell
# Backend
py -3 -m pip install -r requirements.txt
py -3 -m pip install pyinstaller

# Frontend
vp install
vp build
```

### Run (Development)

```powershell
# Production frontend (served from frontend_dist/)
py -3 -m rhythmflow

# Dev mode with Vite+ HMR on port 5173
$env:RHYTHMFLOW_DEV = "1"
py -3 -m rhythmflow
```

### Frontend Dev Only

```powershell
vp dev          # Vite+ dev server on port 5173
vp build        # Vite+ build → rhythmflow/webui/frontend_dist/
vp preview      # Vite+ preview
```

### Build Distribution

```powershell
vp install; vp build
py -3 -m PyInstaller --noconfirm rhythmflow.spec
```

### Run Tests

```powershell
py -3 -m unittest discover -s tests
```

> **Note**: Frontend linting, formatting, type checks, tests, and builds are managed by Vite+. Prefer `vp check` for validation loops.

## Directory Structure

```
RhythmFlow/
├── rhythmflow/                   # Main Python package
│   ├── __init__.py               # Package version (0.2.2)
│   ├── __main__.py               # Entry for `python -m rhythmflow`
│   ├── app.py                    # Main launcher → delegates to webui
│   ├── config.py                 # Constants (SAMPLE_RATE=44100, FFT params, defaults)
│   ├── logging_setup.py          # Logging + Sentry telemetry + exception hooks
│   ├── core/                     # Audio/video processing engine
│   │   ├── alignment.py          # Chroma cross-correlation offset detection
│   │   ├── audio_io.py           # Audio decode via ffmpeg subprocess (f32le)
│   │   ├── ffmpeg_tools.py       # ffmpeg probing, execution, progress parsing
│   │   ├── pipeline.py           # Export pipeline: cut windows, ffmpeg commands
│   │   └── segmented_alignment.py# Smart segmented DP alignment
│   └── webui/                    # pywebview-based desktop UI
│       ├── app_webview.py        # pywebview window creation/lifecycle
│       ├── api.py                # JS-accessible API bridge
│       ├── events.py             # Python→JS event emitter (evaluate_js)
│       ├── lxns.py               # LXNS song library API (maimai/CHUNITHM)
│       ├── media_server.py       # Embedded HTTP server (static files + media tokens)
│       ├── settings.py           # Persistent JSON settings
│       ├── state.py              # AppState: row management, analysis results
│       ├── tasks.py              # Worker threads for analysis/processing
│       ├── waveform.py           # Waveform envelope for review UI
│       └── frontend/             # React + Vite+ frontend source
│           └── src/
│               ├── App.tsx
│               ├── bridge.ts      # Python↔JS bridge (proxy + mock)
│               ├── store.tsx      # React Context + useReducer state
│               ├── types.ts       # TypeScript type interfaces
│               ├── i18n.ts        # Chinese/English translations
│               ├── components/    # UI components (Header, MainPage, modals, review)
│               ├── subtitles.ts
│               └── subtitles.*.txt
├── tests/                         # Python unit tests (unittest)
│   ├── test_alignment.py
│   ├── test_pipeline.py
│   ├── test_lxns.py
│   ├── test_logging_setup.py
│   ├── test_webui_api.py
│   ├── test_webui_state.py
│   └── test_webui_settings.py
├── build/                         # PyInstaller temp files
├── dist/                          # PyInstaller output
├── output/                        # Default aligned video output
├── pyproject.toml                 # Package metadata + setuptools config
├── requirements.txt               # Pip dependencies
├── rhythmflow.spec                # PyInstaller packaging spec
├── README.md
└── .github/workflows/release.yml  # CI/CD release pipeline
```

## Architecture

```
pywebview Desktop Window
├── React Frontend (store.tsx → App.tsx → components/)
│   ├── Calls: window.pywebview.api.*
│   └── Receives: window.rhythmflowBridge.dispatch() events
└── Python Backend
    ├── webui/api.py          — JS-callable API (sync from JS, threaded internally)
    ├── webui/state.py        — AppState (video rows, analysis results, job queue)
    ├── webui/tasks.py        — Worker threads (analysis + processing orchestration)
    ├── webui/events.py       — Fires events to JS via evaluate_js
    ├── webui/media_server.py — Embedded HTTP server on 127.0.0.1:0
    └── core/                 — Processing engine
        ├── alignment.py          → Chroma cross-correlation offset
        ├── segmented_alignment.py → DP local alignment for extra-section detection
        ├── pipeline.py           → Cut windows + ffmpeg export commands
        ├── ffmpeg_tools.py       → ffmpeg probing + execution + progress parsing
        └── audio_io.py           → Audio decode via ffmpeg subprocess (f32le)
```

### Data Flow

1. **Input** — User adds hand-cam videos + reference audio (local or LXNS download)
2. **Analyze** — Decode audio → chromagrams → cross-correlation offset → optional segmented alignment
3. **Review** — Low-confidence results flagged for user review (waveform preview + manual nudge)
4. **Process** — ffmpeg export with cut windows + audio blend (libx264 or stream-copy)
5. **Output** — Aligned MP4 files in the output directory

## Code Conventions

### Python

- `from __future__ import annotations` in all modules (PEP 604 union types)
- **Naming**: `snake_case` functions/variables, `PascalCase` classes, `UPPER_CASE` constants
- **Typing**: Heavily typed — `collections.abc`, `np.ndarray`, `Path`, explicit `TypeAlias`
- **Data classes**: `@dataclass(frozen=True)` for value objects (`AlignmentResult`, `ProcessJob`, `CutWindow`, `MediaProbe`, etc.)
- **Logging**: Module-level `logging.getLogger(__name__)`, no print statements
- **Errors**: Custom exceptions (`FfmpegError`, `AudioDecodeError`, `LxnsError`, `JobBuildError`)
- **No docstrings** — type annotations serve as documentation
- **Broad except**: `except Exception:  # noqa: BLE001` only in per-video error handling

### TypeScript/React

- **State**: React Context + `useReducer` (no external lib)
- **Components**: All functional with hooks
- **i18n**: Simple dictionary in `i18n.ts`
- **Bridge**: Lazy proxy (`liveApi()`) polls for `window.pywebview.api`, with mock fallback for browser dev
- **Strict TypeScript** with explicit interfaces in `types.ts`

### Bridge Communication Pattern

- **JS → Python**: `window.pywebview.api.methodName(args)` (synchronous call to Api class)
- **Python → JS**: `window.evaluate_js("window.rhythmflowBridge.dispatch(eventName, data)")`
- **Media access**: Token-based URLs served by embedded HTTP server

## Testing

- **Framework**: Python `unittest` (stdlib)
- **Run**: `py -3 -m unittest discover -s tests`
- **Patterns**:
  - `TemporaryDirectory` for isolated file I/O
  - `unittest.mock.patch` for network mocking (`urllib.request.urlopen`)
  - Dependency injection for test doubles (e.g., `LxnsReferenceAudioService(fetch_json=..., download_file=...)`)
  - Synthetic audio via numpy/scipy for deterministic DSP tests
  - Sub-tests via `self.subTest(...)` for parameterized cases
- **7 test files** covering: alignment, pipeline, LXNS API, logging, webui API, webui state, webui settings

## Key Files Reference

| File                                     | Purpose                                    |
| ---------------------------------------- | ------------------------------------------ |
| `pyproject.toml`                         | Package metadata, deps, entry point        |
| `requirements.txt`                       | Pip dependencies                           |
| `rhythmflow.spec`                        | PyInstaller packaging                      |
| `rhythmflow/config.py`                   | Global constants                           |
| `rhythmflow/core/alignment.py`           | Chroma offset detection                    |
| `rhythmflow/core/segmented_alignment.py` | Smart trim detection                       |
| `rhythmflow/core/pipeline.py`            | Export pipeline                            |
| `rhythmflow/webui/api.py`                | JS-callable API bridge                     |
| `rhythmflow/webui/state.py`              | Application state machine                  |
| `rhythmflow/webui/tasks.py`              | Worker thread orchestration                |
| `rhythmflow/webui/frontend/src/types.ts` | Frontend type definitions                  |
| `vite.config.ts`                         | Vite+ build, test, lint, and format config |
| `.github/workflows/release.yml`          | CI/CD release pipeline                     |
