"""Tests for report_ai — photo selection and API fallback."""

import pytest
from dataclasses import dataclass


@dataclass
class MockPhoto:
    filename: str
    path: str
    classification: str = "nadir"
    pitch: float = None
    roll: float = None
    yaw: float = None
    latitude: float = None
    longitude: float = None
    altitude: float = None


class TestSelectRepresentativePhotos:
    def setup_method(self):
        from report_ai import select_representative_photos
        self.select = select_representative_photos

    def test_empty_list_returns_empty(self):
        assert self.select([], max_photos=6) == []

    def test_selects_nadir_spread_by_latitude(self):
        photos = [
            MockPhoto("n1.jpg", "/p/n1.jpg", "nadir", latitude=36.82, longitude=-76.4),
            MockPhoto("n2.jpg", "/p/n2.jpg", "nadir", latitude=36.83, longitude=-76.4),
            MockPhoto("n3.jpg", "/p/n3.jpg", "nadir", latitude=36.84, longitude=-76.4),
            MockPhoto("n4.jpg", "/p/n4.jpg", "nadir", latitude=36.85, longitude=-76.4),
        ]
        selected = self.select(photos, max_photos=6)
        # Should pick at least the min/max latitude photos
        lats = [p.latitude for p in selected]
        assert 36.82 in lats
        assert 36.85 in lats

    def test_mixes_nadir_and_oblique(self):
        photos = [
            MockPhoto("n1.jpg", "/p/n1.jpg", "nadir", latitude=36.82, longitude=-76.4),
            MockPhoto("o1.jpg", "/p/o1.jpg", "oblique", yaw=0.0),
            MockPhoto("o2.jpg", "/p/o2.jpg", "oblique", yaw=90.0),
            MockPhoto("o3.jpg", "/p/o3.jpg", "oblique", yaw=180.0),
            MockPhoto("o4.jpg", "/p/o4.jpg", "oblique", yaw=270.0),
        ]
        selected = self.select(photos, max_photos=6)
        classifications = [p.classification for p in selected]
        assert "nadir" in classifications
        assert "oblique" in classifications

    def test_respects_max_photos(self):
        photos = [MockPhoto(f"p{i}.jpg", f"/p/p{i}.jpg", "nadir") for i in range(20)]
        selected = self.select(photos, max_photos=4)
        assert len(selected) <= 4

    def test_handles_all_unknown(self):
        photos = [MockPhoto(f"u{i}.jpg", f"/p/u{i}.jpg", "unknown") for i in range(5)]
        selected = self.select(photos, max_photos=6)
        assert len(selected) > 0


class TestAnalyzePhotos:
    def test_returns_none_without_api_key(self, monkeypatch):
        """Without API key, should return None gracefully."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        from report_ai import analyze_photos
        photos = [MockPhoto("n1.jpg", "/p/n1.jpg")]
        result = analyze_photos(photos, "roof_inspection")
        assert result is None

    def test_returns_none_with_empty_photos(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
        from report_ai import analyze_photos
        result = analyze_photos([], "roof_inspection")
        assert result is None
