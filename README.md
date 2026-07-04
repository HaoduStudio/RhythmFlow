# RhythmFlow

RhythmFlow is a desktop tool for aligning hand-cam arcade rhythm-game videos to a user-provided clean reference audio track. It estimates the global offset with chroma-feature cross-correlation, can run a local smart segmented alignment pass when the reference appears to contain extra sections, and exports a new MP4 with a controllable blend of the original recording audio and the reference track.

## Features

- Batch input for one or more hand-cam videos.
- Reference audio input in any format supported by ffmpeg.
- Automatic chroma-based offset detection with confidence scores.
- Conservative local smart segmented alignment for reference-side or hand-cam-side extra intro, outro, or middle sections.
- Non-contiguous video/reference trimming through ffmpeg `trim`, `atrim`, and `concat`.
- AI-style review signals: smart trim duration, trimmed segment count, confidence, and review status.
- Per-video manual nudge after analysis.
- Accurate re-encode mode and fast stream-copy mode.
- Original/reference volume blend, including full replacement.
- Bundled ffmpeg through `imageio-ffmpeg`; system ffmpeg is used first if available.
- Modern desktop UI built with pywebview + React + Ant Design.
- Chinese and English UI switching.

## Setup

Install the Python dependencies and build the front end (Node.js 18+ required):

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m pip install pyinstaller

cd rhythmflow/webui/frontend
npm install
npm run build
cd ../../..
```

Run the app:

```powershell
py -3 -m rhythmflow
```

## How to use?

1. Add one or more hand-cam videos.
2. Choose the reference audio file.
3. Choose an output directory and filename pattern. The default pattern is `{name}_aligned.mp4`.
4. Pick a cut mode:
   - Accurate re-encode: frame-accurate, best sync, slower.
   - Fast copy: keeps video stream lossless, much faster, cuts near keyframes.
5. Set the original and reference audio volumes.
6. Click Analyze and review the detected offset, confidence, smart trim, AI confidence, and review status.
7. When RhythmFlow flags a row for review, use the review dialog to preview each reference/video segment and confirm it before export.
8. Adjust manual nudge values when needed; reviewed low-confidence rows must be confirmed again after nudge changes.
9. Click Process to export aligned videos.

## Build

Build the front end first, then package the executable:

```powershell
cd rhythmflow/webui/frontend; npm install; npm run build; cd ../../..
py -3 -m PyInstaller --noconfirm rhythmflow.spec
```

This produces a one-folder build. Run it:

```powershell
# Windows
.\dist\RhythmFlow\RhythmFlow.exe
```

On macOS the build is packaged as an app bundle at `dist/RhythmFlow.app`.

## Verification

Run the automated checks:

```powershell
py -3 -m unittest discover -s tests
```