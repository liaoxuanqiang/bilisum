from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


APP_NAME = "BiliSum.app"
APP_EXECUTABLE = "BiliSum"


def run(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command))
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def fail(message: str) -> None:
    raise SystemExit(message)


def require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        fail(f"{label} is missing: {path}")
    return path


def require_dir(path: Path, label: str) -> Path:
    if not path.is_dir():
        fail(f"{label} is missing: {path}")
    return path


def require_executable(path: Path, label: str) -> Path:
    require_file(path, label)
    if not os.access(path, os.X_OK):
        fail(f"{label} is not executable: {path}")
    return path


def expected_mach_arch(arch: str) -> str:
    if arch == "x64":
        return "x86_64"
    if arch == "arm64":
        return "arm64"
    fail(f"Unsupported macOS package arch: {arch}")
    return arch


def require_mach_arch(path: Path, arch: str, label: str) -> None:
    expected = expected_mach_arch(arch)
    result = run(["file", str(path)])
    output = result.stdout.strip()
    if expected not in output:
        fail(f"{label} does not contain expected architecture {expected}: {output}")


def parse_simple_yaml(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def validate_update_config(path: Path, owner: str, repo: str) -> None:
    values = parse_simple_yaml(path)
    expected = {
        "provider": "github",
        "owner": owner,
        "repo": repo,
    }
    mismatches = [
        f"{key}={expected_value!r} (got {values.get(key)!r})"
        for key, expected_value in expected.items()
        if values.get(key) != expected_value
    ]
    if mismatches:
        fail(f"app-update.yml has unexpected update config: {', '.join(mismatches)}")


def runtime_pythonpath(runtime_dir: Path) -> str:
    pth = runtime_dir / "pythonpath.pth"
    if not pth.is_file():
        return ""
    entries: list[str] = []
    for raw_line in pth.read_text(encoding="utf-8").splitlines():
        entry = raw_line.strip()
        if not entry or entry.startswith("#"):
            continue
        entries.append(str(runtime_dir / entry))
    return os.pathsep.join(entries)


def validate_runtime_python(python_executable: Path, runtime_dir: Path) -> None:
    env = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "__PYVENV_LAUNCHER__"):
        env.pop(key, None)
    pythonpath = runtime_pythonpath(runtime_dir)
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    run(
        [
            str(python_executable),
            "-c",
            (
                "import encodings, ensurepip, pip, sqlite3, ssl, sys, video_sum_core; "
                "print(sys.executable)"
            ),
        ],
        env=env,
    )
    run([str(python_executable), "-m", "pip", "--version"], env=env)


def find_app(root: Path) -> Path:
    direct = root / APP_NAME
    if direct.is_dir():
        return direct
    matches = sorted(root.glob("*.app"))
    if not matches:
        fail(f"No .app bundle found under {root}")
    if len(matches) > 1:
        fail(f"Expected one .app bundle under {root}, found: {', '.join(item.name for item in matches)}")
    return matches[0]


def validate_app_bundle(app_path: Path, *, version: str, arch: str, bundle_id: str, owner: str, repo: str) -> None:
    require_dir(app_path, "macOS app bundle")
    contents_dir = require_dir(app_path / "Contents", "app Contents directory")
    macos_dir = require_dir(contents_dir / "MacOS", "app MacOS directory")
    resources_dir = require_dir(contents_dir / "Resources", "app Resources directory")

    info_path = require_file(contents_dir / "Info.plist", "Info.plist")
    with info_path.open("rb") as file:
        info = plistlib.load(file)
    if info.get("CFBundleIdentifier") != bundle_id:
        fail(f"Unexpected bundle id: {info.get('CFBundleIdentifier')!r}")
    if str(info.get("CFBundleShortVersionString") or "") != version:
        fail(f"Unexpected bundle version: {info.get('CFBundleShortVersionString')!r}")

    app_executable_name = str(info.get("CFBundleExecutable") or APP_EXECUTABLE)
    app_executable = require_executable(macos_dir / app_executable_name, "Electron app executable")
    require_mach_arch(app_executable, arch, "Electron app executable")

    app_update_yml = require_file(resources_dir / "app-update.yml", "app-update.yml")
    validate_update_config(app_update_yml, owner, repo)

    backend_dir = require_dir(resources_dir / "backend" / "BiliSum", "packaged backend directory")
    backend_executable = require_executable(backend_dir / APP_EXECUTABLE, "packaged backend executable")
    require_mach_arch(backend_executable, arch, "packaged backend executable")

    runtime_dir = require_dir(backend_dir / "_internal" / "runtime" / "base", "managed base runtime")
    runtime_bin = require_dir(runtime_dir / "bin", "managed runtime bin directory")
    python_candidates = [runtime_bin / "python", runtime_bin / "python3"]
    python_executable = next((item for item in python_candidates if item.is_file()), None)
    if python_executable is None:
        fail(f"Managed runtime Python is missing under {runtime_bin}")
    require_executable(python_executable, "managed runtime Python")
    require_mach_arch(python_executable, arch, "managed runtime Python")
    require_file(runtime_dir / "pythonpath.pth", "managed runtime pythonpath.pth")
    require_file(runtime_dir / "video_sum_runtime.json", "managed runtime metadata")
    validate_runtime_python(python_executable, runtime_dir)


def validate_dmg(dmg_path: Path, *, version: str, arch: str, bundle_id: str, owner: str, repo: str) -> None:
    require_file(dmg_path, "DMG package")
    run(["hdiutil", "verify", str(dmg_path)])
    with tempfile.TemporaryDirectory(prefix="bilisum-dmg-") as mount_dir_raw:
        mount_dir = Path(mount_dir_raw)
        try:
            run(["hdiutil", "attach", str(dmg_path), "-nobrowse", "-readonly", "-mountpoint", str(mount_dir)])
            validate_app_bundle(
                find_app(mount_dir),
                version=version,
                arch=arch,
                bundle_id=bundle_id,
                owner=owner,
                repo=repo,
            )
        finally:
            subprocess.run(["hdiutil", "detach", str(mount_dir), "-force"], check=False)


def validate_zip(zip_path: Path, *, version: str, arch: str, bundle_id: str, owner: str, repo: str) -> None:
    require_file(zip_path, "ZIP package")
    with tempfile.TemporaryDirectory(prefix="bilisum-zip-") as extract_dir_raw:
        extract_dir = Path(extract_dir_raw)
        run(["ditto", "-x", "-k", str(zip_path), str(extract_dir)])
        validate_app_bundle(
            find_app(extract_dir),
            version=version,
            arch=arch,
            bundle_id=bundle_id,
            owner=owner,
            repo=repo,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate BiliSum macOS DMG and ZIP packages.")
    parser.add_argument("--dmg", required=True, type=Path)
    parser.add_argument("--zip", required=True, type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--arch", required=True, choices=("x64", "arm64"))
    parser.add_argument("--bundle-id", default="com.bilisum.desktop")
    parser.add_argument("--update-owner", default="lycohana")
    parser.add_argument("--update-repo", default="BiliSum")
    args = parser.parse_args()

    if platform.system() != "Darwin":
        fail("macOS package validation must run on a macOS runner.")
    if shutil.which("hdiutil") is None:
        fail("hdiutil is required for DMG validation.")
    if shutil.which("ditto") is None:
        fail("ditto is required for ZIP validation.")

    validate_dmg(
        args.dmg,
        version=args.version,
        arch=args.arch,
        bundle_id=args.bundle_id,
        owner=args.update_owner,
        repo=args.update_repo,
    )
    validate_zip(
        args.zip,
        version=args.version,
        arch=args.arch,
        bundle_id=args.bundle_id,
        owner=args.update_owner,
        repo=args.update_repo,
    )
    print(f"macOS package validation passed for {args.arch} {args.version}")


if __name__ == "__main__":
    main()
