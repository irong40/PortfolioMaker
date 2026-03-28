"""Tests for mipmap_service module."""

import os
import time
import threading
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path


class TestCheckMipmap:
    @patch("mipmap_service.MIPMAP_ENGINE")
    def test_check_mipmap_found(self, mock_engine):
        """check_mipmap() returns True when engine path exists."""
        from mipmap_service import check_mipmap
        mock_engine.exists.return_value = True
        # Also mock the gs_dlls check
        with patch("os.path.isdir", return_value=True):
            assert check_mipmap() is True

    @patch("mipmap_service.MIPMAP_ENGINE")
    def test_check_mipmap_missing(self, mock_engine):
        """check_mipmap() returns False when engine path missing."""
        from mipmap_service import check_mipmap
        mock_engine.exists.return_value = False
        with patch("os.path.isdir", return_value=False):
            assert check_mipmap() is False


MOCK_CAMERA_META = [{"id": 1, "meta_data": {"projection_model": 0, "camera_name": "Camera-1",
                     "width": 4000, "height": 3000, "parameters": [2800, 2800, 2000, 1500, 0, 0, 0, 0, 0],
                     "constant_parameters": []}}]
MOCK_IMAGE_META = [{"id": 1, "meta_data": {"width": 4000, "height": 3000, "camera_id": 1,
                    "pos": [-76.3, 36.8, 50.0], "pos_sigma": [2.0, 2.0, 5.0],
                    "orientation": [1, 0, 0, 0, 1, 0, 0, 0, 1], "relative_altitude": 50.0,
                    "focal_length_in_35mm": 24, "pre_calib_param": [2800, 2800, 2000, 1500, 0, 0, 0, 0, 0],
                    "dewarp_flag": False}, "path": "photo_001.jpg"}]


class TestBuildSplatTaskJson:
    @patch("mipmap_service._extract_photo_metadata", return_value=(MOCK_CAMERA_META, MOCK_IMAGE_META))
    def test_build_splat_task_json_defaults(self, mock_meta):
        """build_splat_task_json returns dict with correct default settings."""
        from mipmap_service import build_splat_task_json
        working_dir = Path("C:/tmp/test_work")
        result = build_splat_task_json(working_dir)

        assert result["resolution_level"] == 3
        assert result["mesh_decimate_ratio"] == 0.5
        assert result["generate_gs_ply"] is True
        assert result["generate_gs_splat_sog_tiles"] is True
        # All other generate_* flags should be False
        for key, val in result.items():
            if key.startswith("generate_") and key not in (
                "generate_gs_ply", "generate_gs_splat_sog_tiles"
            ):
                assert val is False, f"{key} should be False, got {val}"

    @patch("mipmap_service._extract_photo_metadata", return_value=(MOCK_CAMERA_META, MOCK_IMAGE_META))
    def test_build_splat_task_json_custom(self, mock_meta):
        """build_splat_task_json(resolution_level=2) overrides default."""
        from mipmap_service import build_splat_task_json
        result = build_splat_task_json(Path("C:/tmp/work"), resolution_level=2)
        assert result["resolution_level"] == 2

    @patch("mipmap_service._extract_photo_metadata", return_value=(MOCK_CAMERA_META, MOCK_IMAGE_META))
    def test_build_splat_task_json_extension_paths(self, mock_meta):
        """extension_paths derived from %APPDATA% not hardcoded username."""
        from mipmap_service import build_splat_task_json
        with patch.dict(os.environ, {"APPDATA": "C:\\Users\\testuser\\AppData\\Roaming"}):
            result = build_splat_task_json(Path("C:/tmp/work"))
            for ext_path in result["extension_paths"]:
                assert "redle" not in ext_path.lower(), "Should not contain hardcoded username"
                assert "testuser" in ext_path, "Should use APPDATA env var"

    @patch("mipmap_service._extract_photo_metadata", return_value=(MOCK_CAMERA_META, MOCK_IMAGE_META))
    def test_build_splat_task_json_working_dir(self, mock_meta):
        """working_dir set to provided path as string."""
        from mipmap_service import build_splat_task_json
        wd = Path("D:/Portfolio/TestSite/2026-03-17/mipmap_work")
        result = build_splat_task_json(wd)
        assert result["working_dir"] == str(wd)


class TestMonitorMipmapLog:
    def test_monitor_parses_progress(self, tmp_path):
        """monitor_mipmap_log reads [Progress] line and calls callback."""
        from mipmap_service import monitor_mipmap_log

        log_file = tmp_path / "log.txt"
        log_file.write_text("[Progress]45.5\n")

        stop_event = threading.Event()
        callback = MagicMock()

        # Run monitor in thread, stop after short delay
        def run_monitor():
            monitor_mipmap_log(log_file, callback, stop_event)

        t = threading.Thread(target=run_monitor, daemon=True)
        t.start()
        time.sleep(0.3)
        stop_event.set()
        t.join(timeout=3)

        callback.assert_called_with(45.5)

    def test_monitor_clamps_100(self, tmp_path):
        """monitor_mipmap_log clamps values to max 100.0."""
        from mipmap_service import monitor_mipmap_log

        log_file = tmp_path / "log.txt"
        log_file.write_text("[Progress]150.0\n")

        stop_event = threading.Event()
        callback = MagicMock()

        t = threading.Thread(
            target=monitor_mipmap_log, args=(log_file, callback, stop_event),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        stop_event.set()
        t.join(timeout=3)

        callback.assert_called_with(100.0)

    def test_monitor_handles_missing_file(self, tmp_path):
        """monitor_mipmap_log does not crash when log file does not exist."""
        from mipmap_service import monitor_mipmap_log

        log_file = tmp_path / "nonexistent.txt"
        stop_event = threading.Event()
        callback = MagicMock()

        t = threading.Thread(
            target=monitor_mipmap_log, args=(log_file, callback, stop_event),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        stop_event.set()
        t.join(timeout=3)

        # Should not crash, callback should not be called
        callback.assert_not_called()


class TestRunMipmapPipeline:
    @patch("mipmap_service.launch_mipmap_stage")
    @patch("mipmap_service.build_splat_task_json")
    def test_run_mipmap_pipeline_creates_dirs(self, mock_build, mock_launch, tmp_path):
        """run_mipmap_pipeline creates working_dir/logs/ before launching."""
        from mipmap_service import run_mipmap_pipeline

        working_dir = tmp_path / "mipmap_work"
        mock_build.return_value = {"working_dir": str(working_dir)}
        mock_launch.return_value = 0

        run_mipmap_pipeline(
            photo_dir=str(tmp_path / "photos"),
            working_dir=str(working_dir),
        )

        logs_dir = working_dir / "logs"
        assert logs_dir.exists(), "logs/ directory should be created"


class TestCopySplatOutputs:
    def test_copy_splat_outputs(self, tmp_path):
        """copy_splat_outputs copies model-gs-ply/ and model-gs-sog-tile/ dirs."""
        from mipmap_service import copy_splat_outputs

        # Create mock source structure
        working_dir = tmp_path / "work"
        three_d = working_dir / "3D"
        gs_ply = three_d / "model-gs-ply"
        gs_sog = three_d / "model-gs-sog-tile"
        gs_ply.mkdir(parents=True)
        gs_sog.mkdir(parents=True)
        (gs_ply / "model.ply").write_text("fake ply")
        (gs_sog / "lod-meta.json").write_text("{}")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir()

        result = copy_splat_outputs(str(working_dir), str(dest_dir))

        assert "model-gs-ply" in result
        assert "model-gs-sog-tile" in result
        assert (Path(result["model-gs-ply"]) / "model.ply").exists()
        assert (Path(result["model-gs-sog-tile"]) / "lod-meta.json").exists()
