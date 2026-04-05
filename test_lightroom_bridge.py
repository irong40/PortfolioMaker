"""
Tests for lightroom_bridge.py — Sortie Lightroom Classic Auto-QA Bridge.
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from lightroom_bridge import (
    push_to_lightroom,
    pull_from_lightroom,
    get_qa_status,
    _is_image,
    _list_images,
)


# ─── FIXTURES ────────────────────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Set up a temp workspace with sorted photos and empty stage dirs."""
    # Create sorted dir with a nadir subfolder containing sample images
    sorted_dir = tmp_path / "sorted"
    nadir_dir = sorted_dir / "nadir"
    nadir_dir.mkdir(parents=True)

    # Create fake image files (content doesn't matter for copy tests)
    for name in ["DJI_0001.jpg", "DJI_0002.jpg", "DJI_0003.jpg", "DJI_0004.tif"]:
        (nadir_dir / name).write_bytes(b"fake image data")

    # Also add a non-image file that should be ignored
    (nadir_dir / "thumbs.db").write_bytes(b"not an image")

    # Stage directories
    watch_dir = tmp_path / "LightroomWatch"
    export_dir = tmp_path / "LightroomExport"
    processing_dir = tmp_path / "ProcessingInput"
    watch_dir.mkdir()
    export_dir.mkdir()
    processing_dir.mkdir()

    # Patch settings to use temp dirs
    settings_file = tmp_path / "sortie_settings.json"
    settings_file.write_text(json.dumps({
        "lightroom_watch_dir": str(watch_dir),
        "lightroom_export_dir": str(export_dir),
        "processing_input_dir": str(processing_dir),
    }))

    import lightroom_bridge
    monkeypatch.setattr(lightroom_bridge, "SETTINGS_FILE", settings_file)

    return {
        "tmp_path": tmp_path,
        "sorted_dir": sorted_dir,
        "nadir_dir": nadir_dir,
        "watch_dir": watch_dir,
        "export_dir": export_dir,
        "processing_dir": processing_dir,
    }


# ─── HELPER TESTS ────────────────────────────────────────────────────────────

class TestHelpers:
    def test_is_image_jpg(self):
        assert _is_image(Path("photo.jpg")) is True

    def test_is_image_jpeg(self):
        assert _is_image(Path("photo.JPEG")) is True

    def test_is_image_tiff(self):
        assert _is_image(Path("photo.tiff")) is True

    def test_is_image_dng(self):
        assert _is_image(Path("photo.DNG")) is True

    def test_is_not_image(self):
        assert _is_image(Path("data.txt")) is False
        assert _is_image(Path("thumbs.db")) is False

    def test_list_images_sorted(self, workspace):
        images = _list_images(workspace["nadir_dir"])
        names = [p.name for p in images]
        assert names == ["DJI_0001.jpg", "DJI_0002.jpg", "DJI_0003.jpg", "DJI_0004.tif"]

    def test_list_images_empty_dir(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        assert _list_images(empty) == []

    def test_list_images_nonexistent_dir(self, tmp_path):
        assert _list_images(tmp_path / "nope") == []


# ─── PUSH TO LIGHTROOM ──────────────────────────────────────────────────────

class TestPushToLightroom:
    def test_copies_nadir_photos(self, workspace):
        result = push_to_lightroom(
            str(workspace["sorted_dir"]),
            "TestSite",
            str(workspace["watch_dir"]),
        )

        assert result["photo_count"] == 4
        assert result["site_name"] == "TestSite"

        dest = Path(result["watch_dir"])
        assert dest.is_dir()
        copied = sorted(f.name for f in dest.iterdir())
        assert "DJI_0001.jpg" in copied
        assert "DJI_0004.tif" in copied
        # Non-image files should not be copied
        assert "thumbs.db" not in copied

    def test_uses_dir_directly_when_no_nadir_subfolder(self, workspace):
        # Put images directly in a flat dir (no nadir subfolder)
        flat_dir = workspace["tmp_path"] / "flat"
        flat_dir.mkdir()
        (flat_dir / "IMG_001.jpg").write_bytes(b"data")
        (flat_dir / "IMG_002.jpg").write_bytes(b"data")

        result = push_to_lightroom(
            str(flat_dir),
            "FlatSite",
            str(workspace["watch_dir"]),
        )
        assert result["photo_count"] == 2

    def test_originals_preserved(self, workspace):
        """Originals must remain in the sorted folder after push."""
        push_to_lightroom(
            str(workspace["sorted_dir"]),
            "TestSite",
            str(workspace["watch_dir"]),
        )
        originals = _list_images(workspace["nadir_dir"])
        assert len(originals) == 4

    def test_empty_source_directory(self, workspace):
        empty = workspace["tmp_path"] / "empty_sorted"
        empty.mkdir()

        result = push_to_lightroom(
            str(empty),
            "EmptySite",
            str(workspace["watch_dir"]),
        )
        assert result["photo_count"] == 0

    def test_creates_site_subfolder(self, workspace):
        push_to_lightroom(
            str(workspace["sorted_dir"]),
            "MySite_2026",
            str(workspace["watch_dir"]),
        )
        assert (workspace["watch_dir"] / "MySite_2026").is_dir()

    def test_uses_settings_default_watch_dir(self, workspace):
        """When watch_dir is None, should use settings value."""
        result = push_to_lightroom(
            str(workspace["sorted_dir"]),
            "DefaultTest",
        )
        expected_dir = workspace["watch_dir"] / "DefaultTest"
        assert result["watch_dir"] == str(expected_dir)
        assert result["photo_count"] == 4


# ─── PULL FROM LIGHTROOM ────────────────────────────────────────────────────

class TestPullFromLightroom:
    def _setup_export(self, workspace, site_name="TestSite", num_keepers=3):
        """Push photos first, then simulate Lightroom export with fewer files."""
        push_to_lightroom(
            str(workspace["sorted_dir"]),
            site_name,
            str(workspace["watch_dir"]),
        )

        export_site = workspace["export_dir"] / site_name
        export_site.mkdir(parents=True, exist_ok=True)
        for i in range(1, num_keepers + 1):
            (export_site / f"DJI_000{i}.jpg").write_bytes(b"exported image")
        return export_site

    def test_renames_with_correct_pattern(self, workspace):
        self._setup_export(workspace, "TestSite", 3)

        result = pull_from_lightroom(
            str(workspace["export_dir"] / "TestSite"),
            "TestSite",
            str(workspace["processing_dir"]),
        )

        assert result["renamed_files"] == [
            "TestSite_0001.jpg",
            "TestSite_0002.jpg",
            "TestSite_0003.jpg",
        ]

    def test_qa_summary_math(self, workspace):
        """4 pushed, 3 exported = 1 reject, 25% rejection rate."""
        self._setup_export(workspace, "MathSite", 3)

        result = pull_from_lightroom(
            str(workspace["export_dir"] / "MathSite"),
            "MathSite",
            str(workspace["processing_dir"]),
        )

        assert result["total_in"] == 4
        assert result["keepers"] == 3
        assert result["rejects"] == 1
        assert abs(result["rejection_rate"] - 0.25) < 0.001

    def test_zero_rejects(self, workspace):
        """All photos kept — 0% rejection rate."""
        self._setup_export(workspace, "PerfectSite", 4)

        result = pull_from_lightroom(
            str(workspace["export_dir"] / "PerfectSite"),
            "PerfectSite",
            str(workspace["processing_dir"]),
        )

        assert result["rejects"] == 0
        assert result["rejection_rate"] == 0.0

    def test_files_copied_to_processing_dir(self, workspace):
        self._setup_export(workspace, "CopySite", 2)

        result = pull_from_lightroom(
            str(workspace["export_dir"] / "CopySite"),
            "CopySite",
            str(workspace["processing_dir"]),
        )

        output_path = Path(result["output_dir"])
        files = sorted(f.name for f in output_path.iterdir())
        assert files == ["CopySite_0001.jpg", "CopySite_0002.jpg"]

    def test_empty_export_directory(self, workspace):
        empty_export = workspace["export_dir"] / "EmptySite"
        empty_export.mkdir()

        result = pull_from_lightroom(
            str(empty_export),
            "EmptySite",
            str(workspace["processing_dir"]),
        )

        assert result["keepers"] == 0
        assert result["renamed_files"] == []
        assert result["rejection_rate"] == 0.0

    def test_deterministic_numbering(self, workspace):
        """Files should be numbered in alphabetical order by original name."""
        export_site = workspace["export_dir"] / "OrderSite"
        export_site.mkdir()
        # Create files in non-alphabetical order
        (export_site / "zebra.jpg").write_bytes(b"z")
        (export_site / "alpha.jpg").write_bytes(b"a")
        (export_site / "middle.jpg").write_bytes(b"m")

        # Also push some to watch dir so total_in is correct
        watch_site = workspace["watch_dir"] / "OrderSite"
        watch_site.mkdir()
        for name in ["zebra.jpg", "alpha.jpg", "middle.jpg"]:
            (watch_site / name).write_bytes(b"x")

        result = pull_from_lightroom(
            str(export_site),
            "OrderSite",
            str(workspace["processing_dir"]),
        )

        # alpha < middle < zebra alphabetically
        assert result["renamed_files"] == [
            "OrderSite_0001.jpg",
            "OrderSite_0002.jpg",
            "OrderSite_0003.jpg",
        ]

    def test_uses_settings_default_output_dir(self, workspace):
        self._setup_export(workspace, "DefaultOut", 2)

        result = pull_from_lightroom(
            str(workspace["export_dir"] / "DefaultOut"),
            "DefaultOut",
        )

        expected = workspace["processing_dir"] / "DefaultOut"
        assert result["output_dir"] == str(expected)


# ─── QA STATUS ───────────────────────────────────────────────────────────────

class TestGetQaStatus:
    def test_empty_pipeline(self, workspace):
        status = get_qa_status("NewSite")
        assert status["stage"] == "empty"
        assert status["watch_count"] == 0
        assert status["export_count"] == 0
        assert status["processing_count"] == 0

    def test_waiting_in_lightroom(self, workspace):
        push_to_lightroom(
            str(workspace["sorted_dir"]),
            "WaitingSite",
            str(workspace["watch_dir"]),
        )
        status = get_qa_status("WaitingSite")
        assert status["stage"] == "waiting_in_lightroom"
        assert status["watch_count"] == 4

    def test_exported_stage(self, workspace):
        export_site = workspace["export_dir"] / "ExportedSite"
        export_site.mkdir()
        (export_site / "photo.jpg").write_bytes(b"data")

        status = get_qa_status("ExportedSite")
        assert status["stage"] == "exported_from_lightroom"

    def test_ready_for_processing(self, workspace):
        proc_site = workspace["processing_dir"] / "ReadySite"
        proc_site.mkdir()
        (proc_site / "ReadySite_0001.jpg").write_bytes(b"data")

        status = get_qa_status("ReadySite")
        assert status["stage"] == "ready_for_processing"
