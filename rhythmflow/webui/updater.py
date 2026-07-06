from __future__ import annotations

from dataclasses import dataclass
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlparse
import urllib.request
import zipfile

from rhythmflow.config import APP_NAME, REPOSITORY_URL

ProgressCallback = Callable[[dict[str, Any]], None]
FetchJson = Callable[[str], dict[str, Any]]
DownloadFile = Callable[[str, Path, ProgressCallback | None], None]


class UpdateError(Exception):
    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key
        self.message = message


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int


@dataclass(frozen=True)
class ReleaseInfo:
    name: str
    tag_name: str
    html_url: str
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class UpdatePlan:
    current_name: str
    latest_name: str
    release_url: str
    asset: ReleaseAsset | None
    update_available: bool


@dataclass(frozen=True)
class Installation:
    platform_key: str
    target_path: Path
    executable_name: str


@dataclass(frozen=True)
class InstallPackage:
    platform_key: str
    archive_path: Path
    target_path: Path
    executable_name: str
    latest_name: str
    asset_name: str


def current_release_name(version: str) -> str:
    cleaned = str(version).strip()
    return cleaned if cleaned.lower().startswith("v") else f"v{cleaned}"


def latest_release_endpoint(repository_url: str = REPOSITORY_URL) -> str:
    parsed = urlparse(repository_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise UpdateError("update_bad_repository", "Could not parse GitHub repository URL.")
    return f"https://api.github.com/repos/{parts[0]}/{parts[1]}/releases/latest"


def build_update_plan(
    current_version: str,
    *,
    platform_key: str | None = None,
    fetch_json: FetchJson | None = None,
) -> UpdatePlan:
    current_name = current_release_name(current_version)
    release = fetch_latest_release(fetch_json=fetch_json)
    if release.name == current_name:
        return UpdatePlan(
            current_name=current_name,
            latest_name=release.name,
            release_url=release.html_url,
            asset=None,
            update_available=False,
        )

    key = platform_key or current_platform_key()
    asset = select_release_asset(release.assets, key)
    return UpdatePlan(
        current_name=current_name,
        latest_name=release.name,
        release_url=release.html_url,
        asset=asset,
        update_available=True,
    )


def fetch_latest_release(*, fetch_json: FetchJson | None = None) -> ReleaseInfo:
    data = (fetch_json or _fetch_json)(latest_release_endpoint())
    name = str(data.get("name") or data.get("tag_name") or "").strip()
    tag_name = str(data.get("tag_name") or "").strip()
    html_url = str(data.get("html_url") or "").strip()
    if not name:
        raise UpdateError("update_bad_release", "Latest GitHub release has no name.")

    assets: list[ReleaseAsset] = []
    for item in data.get("assets") or []:
        asset_name = str(item.get("name") or "").strip()
        download_url = str(item.get("browser_download_url") or "").strip()
        if not asset_name or not download_url:
            continue
        assets.append(
            ReleaseAsset(
                name=asset_name,
                download_url=download_url,
                size=int(item.get("size") or 0),
            )
        )

    return ReleaseInfo(name=name, tag_name=tag_name, html_url=html_url, assets=tuple(assets))


def current_platform_key() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    raise UpdateError("update_unsupported_platform", "Auto-update is available only on Windows and macOS.")


def select_release_asset(assets: tuple[ReleaseAsset, ...], platform_key: str) -> ReleaseAsset:
    platform_key = platform_key.lower()
    candidates = [asset for asset in assets if asset.name.lower().endswith(".zip")]
    if platform_key == "windows":
        candidates = [asset for asset in candidates if "windows" in asset.name.lower()]
        machine = platform.machine().lower()
        if machine in {"amd64", "x86_64"}:
            preferred = [asset for asset in candidates if "x64" in asset.name.lower()]
            if preferred:
                return preferred[0]
    elif platform_key == "macos":
        candidates = [
            asset
            for asset in candidates
            if "macos" in asset.name.lower() or "darwin" in asset.name.lower()
        ]
    else:
        raise UpdateError("update_unsupported_platform", "Auto-update is available only on Windows and macOS.")

    if not candidates:
        raise UpdateError("update_no_asset", "No update package was found for this system.")
    return candidates[0]


def download_update_package(
    plan: UpdatePlan,
    *,
    progress_callback: ProgressCallback | None = None,
    download_file: DownloadFile | None = None,
) -> InstallPackage:
    if not plan.update_available or plan.asset is None:
        raise UpdateError("update_no_update", "No update is available.")

    installation = current_installation()
    temp_dir = Path(tempfile.mkdtemp(prefix="rhythmflow-update-"))
    archive_path = temp_dir / _safe_asset_name(plan.asset.name)
    (download_file or _download_file)(plan.asset.download_url, archive_path, progress_callback)
    validate_update_archive(archive_path, installation.platform_key, installation.executable_name)
    return InstallPackage(
        platform_key=installation.platform_key,
        archive_path=archive_path,
        target_path=installation.target_path,
        executable_name=installation.executable_name,
        latest_name=plan.latest_name,
        asset_name=plan.asset.name,
    )


def current_installation() -> Installation:
    if not getattr(sys, "frozen", False):
        raise UpdateError("update_not_packaged", "Auto-update is available only in packaged builds.")

    executable = Path(sys.executable).resolve()
    if sys.platform == "win32":
        return Installation(
            platform_key="windows",
            target_path=executable.parent,
            executable_name=executable.name,
        )
    if sys.platform == "darwin":
        for path in (executable, *executable.parents):
            if path.suffix == ".app":
                return Installation(platform_key="macos", target_path=path, executable_name="")
        raise UpdateError("update_not_packaged", "Could not locate the running app bundle.")
    raise UpdateError("update_unsupported_platform", "Auto-update is available only on Windows and macOS.")


def validate_update_archive(archive_path: Path, platform_key: str, executable_name: str) -> None:
    if not zipfile.is_zipfile(archive_path):
        raise UpdateError("update_bad_archive", "Downloaded update package is not a valid zip archive.")

    with zipfile.ZipFile(archive_path) as archive:
        names = [info.filename.replace("\\", "/") for info in archive.infolist() if not info.is_dir()]

    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise UpdateError("update_bad_archive", "Downloaded update package contains unsafe paths.")

    lowered = [name.lower() for name in names]
    if platform_key == "windows":
        exe_name = executable_name.lower() or f"{APP_NAME.lower()}.exe"
        if not any(name == exe_name or name.endswith(f"/{exe_name}") for name in lowered):
            raise UpdateError("update_bad_archive", "Windows update package does not contain the app executable.")
        return

    if platform_key == "macos":
        app_prefix = f"{APP_NAME.lower()}.app/"
        if not any(name.startswith(app_prefix) or ".app/contents/macos/" in name for name in lowered):
            raise UpdateError("update_bad_archive", "macOS update package does not contain an app bundle.")
        return

    raise UpdateError("update_unsupported_platform", "Auto-update is available only on Windows and macOS.")


def schedule_update_install(package: InstallPackage) -> None:
    if package.platform_key == "windows":
        _schedule_windows_install(package)
        return
    if package.platform_key == "macos":
        _schedule_macos_install(package)
        return
    raise UpdateError("update_unsupported_platform", "Auto-update is available only on Windows and macOS.")


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except OSError as exc:
        raise UpdateError("update_network_error", str(exc)) from exc
    if not isinstance(payload, dict):
        raise UpdateError("update_bad_release", "GitHub release response was not an object.")
    return payload


def _download_file(url: str, destination: Path, progress_callback: ProgressCallback | None) -> None:
    request = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as file:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback({"downloaded": downloaded, "total": total})
    except OSError as exc:
        raise UpdateError("update_network_error", str(exc)) from exc


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/updater",
    }
    token = os.getenv("RHYTHMFLOW_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _safe_asset_name(name: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name)
    return cleaned or "rhythmflow-update.zip"


def _schedule_windows_install(package: InstallPackage) -> None:
    powershell = shutil.which("powershell.exe") or str(
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    script_path = package.archive_path.parent / "apply_update.ps1"
    script_path.write_text(_WINDOWS_INSTALL_SCRIPT, encoding="utf-8")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script_path),
            str(os.getpid()),
            str(package.archive_path),
            str(package.target_path),
            package.executable_name,
        ],
        cwd=tempfile.gettempdir(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def _schedule_macos_install(package: InstallPackage) -> None:
    script_path = package.archive_path.parent / "apply_update.sh"
    script_path.write_text(_MACOS_INSTALL_SCRIPT, encoding="utf-8", newline="\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
    subprocess.Popen(
        [
            "/bin/sh",
            str(script_path),
            str(os.getpid()),
            str(package.archive_path),
            str(package.target_path),
        ],
        cwd=tempfile.gettempdir(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


_WINDOWS_INSTALL_SCRIPT = r"""
param(
    [int]$TargetPid,
    [string]$ArchivePath,
    [string]$TargetDir,
    [string]$ExecutableName
)

$ErrorActionPreference = "Stop"
Wait-Process -Id $TargetPid -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 600

$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("rhythmflow-update-" + [guid]::NewGuid().ToString("N"))
$backup = "$TargetDir.old-" + [guid]::NewGuid().ToString("N")

try {
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $staging -Force
    $source = Join-Path $staging "RhythmFlow"
    if (-not (Test-Path -LiteralPath $source)) {
        $dirs = @(Get-ChildItem -LiteralPath $staging -Directory)
        if ($dirs.Count -eq 1) {
            $source = $dirs[0].FullName
        } else {
            $source = $staging
        }
    }
    $nextExe = Join-Path $source $ExecutableName
    if (-not (Test-Path -LiteralPath $nextExe)) {
        throw "Updated executable not found: $nextExe"
    }
    if (Test-Path -LiteralPath $TargetDir) {
        Move-Item -LiteralPath $TargetDir -Destination $backup -Force
    }
    Move-Item -LiteralPath $source -Destination $TargetDir -Force
    Start-Process -FilePath (Join-Path $TargetDir $ExecutableName) -WorkingDirectory $TargetDir
    if (Test-Path -LiteralPath $backup) {
        Remove-Item -LiteralPath $backup -Recurse -Force
    }
} catch {
    if ((-not (Test-Path -LiteralPath $TargetDir)) -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $TargetDir -Force
    }
    throw
} finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}
"""


_MACOS_INSTALL_SCRIPT = r"""#!/bin/sh
set -eu

TARGET_PID="$1"
ARCHIVE_PATH="$2"
TARGET_PATH="$3"

while kill -0 "$TARGET_PID" 2>/dev/null; do
    sleep 0.2
done
sleep 0.6

STAGING="$(mktemp -d "${TMPDIR:-/tmp}/rhythmflow-update.XXXXXX")"
BACKUP="${TARGET_PATH}.old-$(date +%s)"

cleanup() {
    if [ -d "$STAGING" ]; then
        rm -rf "$STAGING"
    fi
}
trap cleanup EXIT

/usr/bin/ditto -x -k "$ARCHIVE_PATH" "$STAGING"
APP_NAME="$(basename "$TARGET_PATH")"
SOURCE="$STAGING/$APP_NAME"
if [ ! -d "$SOURCE" ]; then
    SOURCE="$(find "$STAGING" -maxdepth 1 -type d -name '*.app' -print -quit)"
fi
if [ -z "$SOURCE" ] || [ ! -d "$SOURCE" ]; then
    exit 1
fi

if [ -d "$TARGET_PATH" ]; then
    mv "$TARGET_PATH" "$BACKUP"
fi

if ! mv "$SOURCE" "$TARGET_PATH"; then
    if [ ! -d "$TARGET_PATH" ] && [ -d "$BACKUP" ]; then
        mv "$BACKUP" "$TARGET_PATH"
    fi
    exit 1
fi

/usr/bin/open "$TARGET_PATH"
rm -rf "$BACKUP"
"""
