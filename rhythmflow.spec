# -*- mode: python ; coding: utf-8 -*-

import os
import re
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules


# Keep the bundle version in sync with the package.
with open(os.path.join(SPECPATH, "rhythmflow", "__init__.py"), encoding="utf-8") as _fh:
    _match = re.search(r'__version__\s*=\s*"([^"]+)"', _fh.read())
APP_VERSION = _match.group(1) if _match else "0.0.0"


datas = []
binaries = []
hiddenimports = []

for package_name in ("webview", "imageio_ffmpeg", "sentry_sdk"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package_name)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

hiddenimports += collect_submodules("scipy._external.array_api_compat.numpy")
# pywebview loads its GUI backend lazily; make sure the desktop ones ship.
hiddenimports += [
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
    "webview.platforms.cocoa",
    "clr",
]

# Bundle the built React front end. Run `vp build` in the workspace root before
# packaging.
frontend_dist = os.path.join(SPECPATH, "rhythmflow", "webui", "frontend_dist")
if not os.path.isfile(os.path.join(frontend_dist, "index.html")):
    raise SystemExit(
        "frontend_dist/index.html not found. Build the UI first:\n"
        "  vp install && vp build"
    )
datas += [(frontend_dist, os.path.join("rhythmflow", "webui", "frontend_dist"))]


a = Analysis(
    ["rhythmflow/__main__.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# One-dir build: a launcher executable plus its dependencies in a folder.
# (No single-file exe — onefile is slower to start and less reliable for the
# bundled WebView2/WKWebView native libraries.) UPX is disabled so it never
# rewrites those native DLLs.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RhythmFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RhythmFlow",
)

# On macOS, wrap the one-dir output into a proper .app bundle so pywebview's
# WKWebView backend can create a window.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="RhythmFlow.app",
        icon=None,
        bundle_identifier="com.haodustudio.rhythmflow",
        version=APP_VERSION,
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.video",
        },
    )
