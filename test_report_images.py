"""Tests for report_images — thumbnail generation and image preparation."""

import os
import pytest
from PIL import Image
from dataclasses import dataclass


@dataclass
class MockPhoto:
    filename: str
    path: str
    classification: str = "nadir"
    pitch: float = None
    yaw: float = None
    latitude: float = None
    longitude: float = None
    altitude: float = None


class TestGenerateThumbnail:
    def test_creates_jpeg_thumbnail(self, tmp_path):
        from report_images import generate_thumbnail

        src = tmp_path / "photo.jpg"
        Image.new("RGB", (4000, 3000), (100, 150, 200)).save(str(src))

        thumb = generate_thumbnail(str(src), str(tmp_path / "thumbs"))
        assert thumb is not None
        assert thumb.endswith(".jpg")
        assert os.path.exists(thumb)

        img = Image.open(thumb)
        assert img.size[0] <= 800
        assert img.size[1] <= 600

    def test_handles_rgba_image(self, tmp_path):
        from report_images import generate_thumbnail

        src = tmp_path / "photo.png"
        Image.new("RGBA", (2000, 1500), (100, 150, 200, 128)).save(str(src))

        thumb = generate_thumbnail(str(src), str(tmp_path / "thumbs"))
        assert thumb is not None
        assert Image.open(thumb).mode == "RGB"

    def test_returns_none_for_missing_file(self, tmp_path):
        from report_images import generate_thumbnail
        result = generate_thumbnail("/nonexistent/photo.jpg", str(tmp_path))
        assert result is None

    def test_custom_max_size(self, tmp_path):
        from report_images import generate_thumbnail

        src = tmp_path / "photo.jpg"
        Image.new("RGB", (4000, 3000), (100, 150, 200)).save(str(src))

        thumb = generate_thumbnail(str(src), str(tmp_path / "thumbs"), max_size=(400, 300))
        img = Image.open(thumb)
        assert img.size[0] <= 400
        assert img.size[1] <= 300


class TestSelectReportPhotos:
    def test_selects_mix_of_types(self, tmp_path):
        from report_images import select_report_photos

        photos = []
        for i in range(5):
            p = tmp_path / f"nadir_{i}.jpg"
            Image.new("RGB", (100, 100)).save(str(p))
            photos.append(MockPhoto(f"nadir_{i}.jpg", str(p), "nadir",
                                    latitude=36.82 + i * 0.001))
        for i in range(5):
            p = tmp_path / f"oblique_{i}.jpg"
            Image.new("RGB", (100, 100)).save(str(p))
            photos.append(MockPhoto(f"oblique_{i}.jpg", str(p), "oblique",
                                    yaw=i * 90.0))

        selected = select_report_photos(photos, max_photos=4)
        assert len(selected) <= 4
        types = {p.classification for p in selected}
        assert "nadir" in types
        assert "oblique" in types

    def test_respects_max_photos(self, tmp_path):
        from report_images import select_report_photos

        photos = []
        for i in range(20):
            p = tmp_path / f"p_{i}.jpg"
            Image.new("RGB", (100, 100)).save(str(p))
            photos.append(MockPhoto(f"p_{i}.jpg", str(p), "nadir"))

        selected = select_report_photos(photos, max_photos=3)
        assert len(selected) <= 3


class TestPrepareReportImages:
    def test_full_preparation(self, tmp_path):
        from report_images import prepare_report_images

        photos = []
        for i in range(4):
            p = tmp_path / f"photo_{i}.jpg"
            Image.new("RGB", (2000, 1500), (50 + i * 30, 80, 120)).save(str(p))
            photos.append(MockPhoto(f"photo_{i}.jpg", str(p), "nadir",
                                    pitch=-85.0, altitude=120.0))

        result = prepare_report_images(photos, None, None, str(tmp_path / "out"))
        assert "photo_thumbs" in result
        assert len(result["photo_thumbs"]) > 0
        for path, caption in result["photo_thumbs"]:
            assert os.path.exists(path)
            assert "Nadir" in caption

    def test_with_no_photos(self, tmp_path):
        from report_images import prepare_report_images
        result = prepare_report_images([], None, None, str(tmp_path / "out"))
        assert result["photo_thumbs"] == []
        assert result["ortho_preview"] is None


class TestDSMPreview:
    def test_creates_colorized_preview(self, tmp_path):
        from report_images import generate_dsm_preview
        import numpy as np

        # Create a fake 16-bit DSM TIFF
        arr = np.random.uniform(10, 50, (200, 300)).astype(np.float32)
        img = Image.fromarray(arr, mode="F")
        dsm_path = tmp_path / "dsm.tif"
        img.save(str(dsm_path))

        preview = generate_dsm_preview(str(dsm_path), str(tmp_path / "thumbs"))
        assert preview is not None
        assert os.path.exists(preview)
        assert preview.endswith(".jpg")

    def test_returns_none_for_missing_file(self, tmp_path):
        from report_images import generate_dsm_preview
        assert generate_dsm_preview(None, str(tmp_path)) is None
        assert generate_dsm_preview("/nonexistent.tif", str(tmp_path)) is None
