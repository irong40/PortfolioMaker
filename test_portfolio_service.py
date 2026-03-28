"""Tests for portfolio_service orchestration layer."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from portfolio_service import (
    check_nodeodm,
    scan_for_job,
    build_output_dir,
    write_site_info,
    portfolio_only,
    PORTFOLIO_ROOT,
)
from odm_presets import get_preset
from photo_classifier import PhotoMeta, ClassificationResult


def _make_result(tmp_path, photos):
    nadir = sum(1 for p in photos if p.classification == "nadir")
    oblique = sum(1 for p in photos if p.classification == "oblique")
    return ClassificationResult(
        source_dir=str(tmp_path), total=len(photos),
        nadir_count=nadir, oblique_count=oblique, photos=photos,
        threshold=-70.0,
    )


def _photo(tmp_path, name, pitch, classification):
    p = tmp_path / name
    p.write_bytes(b"fake jpeg")
    return PhotoMeta(
        filename=name, path=str(p),
        pitch=pitch, latitude=36.82, longitude=-76.42,
        classification=classification,
    )


# ─── check_nodeodm ────────────────────────────────────────────────────────


class TestCheckNodeodm:
    @patch("sentinel_core.nodeodm._get_requests")
    def test_reachable(self, mock_get_requests):
        mock_requests = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"version": "2.0", "taskQueueCount": 0}
        mock_requests.get.return_value = mock_resp
        mock_get_requests.return_value = mock_requests
        result = check_nodeodm("http://localhost:3000")
        assert result is not None
        assert result["version"] == "2.0"

    @patch("sentinel_core.nodeodm._get_requests")
    def test_unreachable(self, mock_get_requests):
        mock_requests = MagicMock()
        mock_requests.RequestException = Exception
        mock_requests.get.side_effect = Exception("refused")
        mock_get_requests.return_value = mock_requests
        result = check_nodeodm("http://localhost:3000")
        assert result is None


# ─── scan_for_job ──────────────────────────────────────────────────────────


class TestScanForJob:
    def test_nadir_filter_returns_nadir_only(self, tmp_path):
        photos = [
            _photo(tmp_path, "n1.jpg", -90.0, "nadir"),
            _photo(tmp_path, "o1.jpg", -45.0, "oblique"),
        ]
        result = _make_result(tmp_path, photos)
        preset = get_preset("construction_progress")
        filtered = scan_for_job(result, preset)
        assert filtered.total == 1
        assert filtered.nadir_count == 1

    def test_none_filter_returns_all(self, tmp_path):
        photos = [
            _photo(tmp_path, "n1.jpg", -90.0, "nadir"),
            _photo(tmp_path, "o1.jpg", -45.0, "oblique"),
        ]
        result = _make_result(tmp_path, photos)
        preset = get_preset("roof_inspection")
        filtered = scan_for_job(result, preset)
        assert filtered.total == 2


# ─── build_output_dir ──────────────────────────────────────────────────────


class TestBuildOutputDir:
    def test_includes_site_and_date(self):
        path = build_output_dir("MallTest", "2026-03-16")
        assert "MallTest" in path
        assert "2026-03-16" in path

    def test_uses_portfolio_root(self):
        path = build_output_dir("TestSite", "2026-01-01")
        assert path.startswith(PORTFOLIO_ROOT)

    def test_auto_date(self):
        path = build_output_dir("TestSite")
        # Should not raise, and should contain the site name
        assert "TestSite" in path


# ─── write_site_info ───────────────────────────────────────────────────────


class TestWriteSiteInfo:
    def test_writes_json(self, tmp_path):
        output_dir = tmp_path / "MallTest" / "2026-03-16"
        output_dir.mkdir(parents=True)
        write_site_info(str(output_dir), "MallTest", "construction_progress")

        info_path = tmp_path / "MallTest" / "site_info.json"
        assert info_path.exists()
        data = json.loads(info_path.read_text())
        assert data["site_name"] == "MallTest"
        assert data["job_type"] == "construction_progress"
        assert "updated_at" in data

    def test_tracks_visits(self, tmp_path):
        output_dir = tmp_path / "MallTest" / "2026-03-16"
        output_dir.mkdir(parents=True)
        write_site_info(str(output_dir), "MallTest", "construction_progress")

        output_dir2 = tmp_path / "MallTest" / "2026-03-23"
        output_dir2.mkdir(parents=True)
        write_site_info(str(output_dir2), "MallTest", "construction_progress")

        info_path = tmp_path / "MallTest" / "site_info.json"
        data = json.loads(info_path.read_text())
        assert "2026-03-16" in data["visits"]
        assert "2026-03-23" in data["visits"]

    def test_merges_with_existing(self, tmp_path):
        output_dir = tmp_path / "MallTest" / "2026-03-16"
        output_dir.mkdir(parents=True)
        info_path = tmp_path / "MallTest" / "site_info.json"
        info_path.write_text(json.dumps({"custom_field": "preserved"}))

        write_site_info(str(output_dir), "MallTest", "construction_progress")
        data = json.loads(info_path.read_text())
        assert data["custom_field"] == "preserved"
        assert data["site_name"] == "MallTest"


# ─── portfolio_only ────────────────────────────────────────────────────────


class TestPortfolioOnly:
    @patch("portfolio_service.build_output_dir")
    def test_sorts_and_writes_manifest(self, mock_output_dir, tmp_path):
        mock_output_dir.return_value = str(tmp_path / "output")

        # Create fake photos
        photos_dir = tmp_path / "photos"
        photos_dir.mkdir()
        (photos_dir / "DJI_0001.jpg").write_bytes(b"fake")
        (photos_dir / "DJI_0002.jpg").write_bytes(b"fake")

        # Mock classify_photos to return controlled result
        with patch("portfolio_service.classify_photos") as mock_classify:
            photos = [
                PhotoMeta(filename="DJI_0001.jpg", path=str(photos_dir / "DJI_0001.jpg"),
                          pitch=-90.0, classification="nadir"),
                PhotoMeta(filename="DJI_0002.jpg", path=str(photos_dir / "DJI_0002.jpg"),
                          pitch=-45.0, classification="oblique"),
            ]
            mock_classify.return_value = ClassificationResult(
                source_dir=str(photos_dir), total=2,
                nadir_count=1, oblique_count=1, photos=photos, threshold=-70.0,
            )

            result = portfolio_only(str(photos_dir), "real_estate", "TestSite")

        assert "error" not in result
        assert result["task_uuid"] is None
        assert result["downloaded"] == {}

    @patch("portfolio_service.build_output_dir")
    def test_empty_folder_returns_error(self, mock_output_dir, tmp_path):
        mock_output_dir.return_value = str(tmp_path / "output")

        with patch("portfolio_service.classify_photos") as mock_classify:
            mock_classify.return_value = ClassificationResult(
                source_dir=str(tmp_path), total=0, photos=[],
            )
            result = portfolio_only(str(tmp_path), "real_estate", "TestSite")

        assert "error" in result
