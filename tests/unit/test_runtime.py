"""Unit tests for video_sum_infra.runtime module core functions."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from video_sum_infra import runtime as runtime_module
from video_sum_infra.runtime import (
    ffmpeg_location,
    read_runtime_metadata,
    runtime_python_candidates,
    runtime_site_packages_dir,
    sanitized_subprocess_dll_search,
    write_runtime_metadata,
)
from video_sum_service.runtime_support import normalize_runtime_channel


# ---------------------------------------------------------------------------
# runtime_python_candidates
# ---------------------------------------------------------------------------

def test_runtime_python_candidates_returns_correct_candidates() -> None:
    candidates = runtime_python_candidates(Path("/fake/runtime"))

    assert isinstance(candidates, list)
    assert len(candidates) == 5
    assert Path("/fake/runtime/python.exe") in candidates
    assert Path("/fake/runtime/Scripts/python.exe") in candidates
    assert Path("/fake/runtime/bin/python") in candidates
    assert Path("/fake/runtime/bin/python3") in candidates
    assert Path("/fake/runtime/python") in candidates


# ---------------------------------------------------------------------------
# ffmpeg_location
# ---------------------------------------------------------------------------

def test_ffmpeg_location_returns_none_when_no_ffmpeg() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with patch("video_sum_infra.runtime.shutil.which", return_value=None):
            assert ffmpeg_location() is None


# ---------------------------------------------------------------------------
# read_runtime_metadata / write_runtime_metadata
# ---------------------------------------------------------------------------

def test_read_runtime_metadata_returns_empty_dict_for_missing_dir(tmp_path: Path) -> None:
    nonexistent = tmp_path / "nonexistent"
    result = read_runtime_metadata(nonexistent)
    assert result == {}


def test_write_and_read_runtime_metadata_roundtrip(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIDEO_SUM_APP_DATA_ROOT", str(tmp_path))
    runtime_dir = tmp_path / "runtime" / "base"
    runtime_dir.mkdir(parents=True)

    payload = {"runtimeChannel": "base", "appVersion": "1.0.0"}
    write_runtime_metadata("base", payload)

    result = read_runtime_metadata(runtime_dir)
    assert result.get("runtimeChannel") == "base"
    assert result.get("appVersion") == "1.0.0"


def test_write_runtime_metadata_merges_existing_keys(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIDEO_SUM_APP_DATA_ROOT", str(tmp_path))
    runtime_dir = tmp_path / "runtime" / "test"
    runtime_dir.mkdir(parents=True)

    # Pre-create existing metadata
    existing = {"existingKey": "original", "sharedKey": "original"}
    (runtime_dir / "video_sum_runtime.json").write_text(json.dumps(existing), encoding="utf-8")

    # Write new payload (partial update)
    write_runtime_metadata("test", {"sharedKey": "updated", "newKey": "new"})

    result = read_runtime_metadata(runtime_dir)
    assert result["existingKey"] == "original"
    assert result["sharedKey"] == "updated"
    assert result["newKey"] == "new"


# ---------------------------------------------------------------------------
# runtime_site_packages_dir
# ---------------------------------------------------------------------------

def test_runtime_site_packages_dir_ends_with_site_packages(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime" / "base"
    lib_dir = runtime_dir / "lib"
    version_dir = lib_dir / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    version_dir.mkdir(parents=True)

    with patch.object(runtime_module, "managed_runtime_dir", return_value=runtime_dir):
        result = runtime_site_packages_dir("base")

    assert result.name == "site-packages"
    assert str(result).endswith("site-packages")


# ---------------------------------------------------------------------------
# sanitized_subprocess_dll_search
# ---------------------------------------------------------------------------

def test_sanitized_subprocess_dll_search_non_windows_noop() -> None:
    """On non-Windows the context manager is a no-op."""
    with patch.object(os, "name", "posix"):
        with sanitized_subprocess_dll_search():
            pass


def test_sanitized_subprocess_dll_search_windows_resets_dll_directory() -> None:
    mock_kernel32 = MagicMock()
    mock_kernel32.kernel32.GetDllDirectoryW.return_value = 12
    mock_kernel32.kernel32.SetDllDirectoryW.return_value = 1

    prev_buffer = MagicMock()
    prev_buffer.value = "C:\\old_dll_dir"

    with patch.object(os, "name", "nt"):
        with patch.object(runtime_module, "is_frozen", return_value=True):
            with patch.object(runtime_module.ctypes, "windll", mock_kernel32, create=True):
                with patch.object(runtime_module.ctypes, "create_unicode_buffer", return_value=prev_buffer):
                    with sanitized_subprocess_dll_search():
                        pass

    # SetDllDirectoryW called twice on kernel32: reset to None, then restore previous path
    set_calls = mock_kernel32.kernel32.SetDllDirectoryW.call_args_list
    assert len(set_calls) == 2
    assert set_calls[0][0][0] is None
    assert set_calls[1][0][0] == "C:\\old_dll_dir"


# ---------------------------------------------------------------------------
# normalize_runtime_channel
# ---------------------------------------------------------------------------

def test_normalize_runtime_channel_base() -> None:
    assert normalize_runtime_channel("base") == "base"
    assert normalize_runtime_channel("BASE") == "base"
    assert normalize_runtime_channel(None) == "base"
    assert normalize_runtime_channel("") == "base"
    assert normalize_runtime_channel("default") == "base"


def test_normalize_runtime_channel_gpu_cu128() -> None:
    assert normalize_runtime_channel("gpu-cu128") == "gpu-cu128"
    assert normalize_runtime_channel("gpu-cu126") == "gpu-cu126"


def test_normalize_runtime_channel_unknown_raises() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="Unsupported runtime channel"):
        normalize_runtime_channel("unknown")


def test_normalize_runtime_channel_unknown_gpu_allowed() -> None:
    result = normalize_runtime_channel("gpu-cu999", allow_unknown_gpu=True)
    assert result == "gpu-cu999"


def test_normalize_runtime_channel_unknown_gpu_not_allowed() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException, match="Unsupported runtime channel"):
        normalize_runtime_channel("gpu-cu999")
