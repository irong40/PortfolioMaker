"""Tests for drive_delivery — delivery file collection policy."""

from drive_delivery import collect_delivery_files


class TestCollectDeliveryFiles:
    def test_skips_underscore_folders(self, tmp_path):
        (tmp_path / "report.pdf").write_text("x")
        (tmp_path / "gis").mkdir()
        (tmp_path / "gis" / "mission.kml").write_text("x")
        (tmp_path / "_gis").mkdir()
        (tmp_path / "_gis" / "photo_points.csv").write_text("x")
        (tmp_path / "_report_thumbs").mkdir()
        (tmp_path / "_report_thumbs" / "thumb.jpg").write_text("x")
        files = {f.relative_to(tmp_path).as_posix()
                 for f in collect_delivery_files(tmp_path)}
        assert files == {"report.pdf", "gis/mission.kml"}

    def test_skips_nested_underscore_folders(self, tmp_path):
        (tmp_path / "panoramas" / "_work").mkdir(parents=True)
        (tmp_path / "panoramas" / "_work" / "tmp.jpg").write_text("x")
        (tmp_path / "panoramas" / "pano.jpg").write_text("x")
        files = {f.relative_to(tmp_path).as_posix()
                 for f in collect_delivery_files(tmp_path)}
        assert files == {"panoramas/pano.jpg"}

    def test_empty_dir(self, tmp_path):
        assert collect_delivery_files(tmp_path) == []
