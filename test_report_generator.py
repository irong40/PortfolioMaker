"""Tests for report_generator — covers AI-enhanced and fallback modes."""

import os
import pytest
from report_generator import generate_report, REPORT_TYPES, REPORTLAB_AVAILABLE


class TestReportTypes:
    def test_all_types_registered(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate", "gaussian_splat",
        }
        assert set(REPORT_TYPES.keys()) == expected

    def test_all_types_have_titles(self):
        for key, title in REPORT_TYPES.items():
            assert isinstance(title, str)
            assert len(title) > 5


SAMPLE_DATA = {
    "site_name": "MallTest",
    "date": "2026-03-16",
    "job_type": "construction_progress",
    "total_photos": 443,
    "nadir_count": 380,
    "oblique_count": 63,
    "platform": "DJI Mini 3 Pro",
    "gps_bounds": (36.824, 36.828, -76.418, -76.412),
    "ortho_path": None,
    "dsm_path": None,
    "downloads": {},
}

SAMPLE_AI_ANALYSIS = {
    "executive_summary": "The construction site shows active earthwork in the southeast quadrant with foundation forms visible in the northwest.",
    "observations": [
        {"finding": "Active earthwork with exposed soil in SE quadrant", "location": "Southeast", "severity": "info"},
        {"finding": "Foundation forms set, ready for pour", "location": "Northwest corner", "severity": "info"},
        {"finding": "Erosion control silt fence damaged along south boundary", "location": "South perimeter", "severity": "moderate"},
        {"finding": "Material staging area well-organized", "location": "Northeast", "severity": "info"},
    ],
    "conditions": [
        "Clear weather, good visibility",
        "Soil appears dry — recent grading work",
        "Active construction equipment on site",
    ],
    "recommendations": [
        "Repair silt fence along south boundary before next rain event",
        "Schedule follow-up flight after foundation pour for progress comparison",
        "Consider additional oblique captures of northwest foundation area",
    ],
    "photo_notes": [
        {"photo_index": 1, "description": "Overview showing full site from south"},
        {"photo_index": 2, "description": "Foundation forms in northwest corner"},
    ],
}

SAMPLE_IMAGES = {
    "photo_thumbs": [],
    "ortho_preview": None,
    "dsm_preview": None,
}


@pytest.mark.skipif(not REPORTLAB_AVAILABLE, reason="reportlab not installed")
class TestGenerateReport:
    """Test PDF generation in fallback mode (no AI, no images)."""

    def test_construction_progress_creates_pdf(self, tmp_path):
        result = generate_report("construction_progress", SAMPLE_DATA, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])
        assert result["pdf_path"].endswith(".pdf")
        assert os.path.getsize(result["pdf_path"]) > 1000

    def test_property_survey_creates_pdf(self, tmp_path):
        data = {**SAMPLE_DATA, "job_type": "property_survey"}
        result = generate_report("property_survey", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])

    def test_roof_inspection_creates_pdf(self, tmp_path):
        data = {**SAMPLE_DATA, "job_type": "roof_inspection"}
        result = generate_report("roof_inspection", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])

    def test_structures_creates_pdf(self, tmp_path):
        data = {**SAMPLE_DATA, "job_type": "structures"}
        result = generate_report("structures", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])

    def test_vegetation_creates_pdf(self, tmp_path):
        data = {**SAMPLE_DATA, "job_type": "vegetation"}
        result = generate_report("vegetation", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])

    def test_real_estate_creates_pdf(self, tmp_path):
        data = {**SAMPLE_DATA, "job_type": "real_estate"}
        result = generate_report("real_estate", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])

    def test_unknown_type_returns_none(self, tmp_path):
        result = generate_report("nonexistent", SAMPLE_DATA, str(tmp_path))
        assert result is None

    def test_pdf_filename_contains_site_and_type(self, tmp_path):
        result = generate_report("construction_progress", SAMPLE_DATA, str(tmp_path))
        filename = os.path.basename(result["pdf_path"])
        assert "MallTest" in filename
        assert "construction_progress" in filename
        assert "2026-03-16" in filename

    def test_report_with_downloads(self, tmp_path):
        fake_ortho = tmp_path / "orthophoto.tif"
        fake_ortho.write_bytes(b"x" * 10000)
        data = {**SAMPLE_DATA, "downloads": {"orthophoto.tif": str(fake_ortho)}}
        result = generate_report("construction_progress", data, str(tmp_path))
        assert result is not None

    def test_report_without_gps(self, tmp_path):
        data = {**SAMPLE_DATA, "gps_bounds": None}
        result = generate_report("property_survey", data, str(tmp_path))
        assert result is not None

    def test_gaussian_splat_creates_pdf(self, tmp_path):
        data = {**SAMPLE_DATA, "job_type": "gaussian_splat", "engine": "mipmap",
                "mipmap_settings": {"resolution_level": 3, "mesh_decimate_ratio": 0.5}}
        result = generate_report("gaussian_splat", data, str(tmp_path))
        assert result is not None
        assert os.path.exists(result["pdf_path"])
        assert "gaussian_splat" in result["pdf_path"]

    def test_all_seven_types_produce_unique_pdfs(self, tmp_path):
        pdfs = []
        for report_type in REPORT_TYPES:
            data = {**SAMPLE_DATA, "job_type": report_type}
            result = generate_report(report_type, data, str(tmp_path))
            assert result is not None, f"{report_type} failed"
            pdfs.append(result["pdf_path"])
        assert len(set(pdfs)) == 7


@pytest.mark.skipif(not REPORTLAB_AVAILABLE, reason="reportlab not installed")
class TestAIEnhancedReport:
    """Test PDF generation with AI analysis data injected."""

    def test_ai_report_creates_larger_pdf(self, tmp_path):
        data_no_ai = {**SAMPLE_DATA}
        data_ai = {**SAMPLE_DATA, "ai_analysis": SAMPLE_AI_ANALYSIS, "images": SAMPLE_IMAGES}

        result_no_ai = generate_report("construction_progress", data_no_ai, str(tmp_path / "no_ai"))
        result_ai = generate_report("construction_progress", data_ai, str(tmp_path / "ai"))

        assert result_no_ai is not None
        assert result_ai is not None
        # AI report should be larger (more content)
        assert os.path.getsize(result_ai["pdf_path"]) > os.path.getsize(result_no_ai["pdf_path"])

    def test_ai_report_with_empty_observations(self, tmp_path):
        """AI returned but no observations — should fall back to static."""
        data = {**SAMPLE_DATA, "ai_analysis": {"observations": []}, "images": SAMPLE_IMAGES}
        result = generate_report("construction_progress", data, str(tmp_path))
        assert result is not None

    def test_ai_report_all_types(self, tmp_path):
        """All 7 types should work with AI analysis."""
        for report_type in REPORT_TYPES:
            data = {**SAMPLE_DATA, "job_type": report_type,
                    "ai_analysis": SAMPLE_AI_ANALYSIS, "images": SAMPLE_IMAGES}
            result = generate_report(report_type, data, str(tmp_path / report_type))
            assert result is not None, f"{report_type} failed with AI"

    def test_ai_report_with_none_ai_analysis(self, tmp_path):
        """Explicit None ai_analysis should produce fallback report."""
        data = {**SAMPLE_DATA, "ai_analysis": None}
        result = generate_report("roof_inspection", data, str(tmp_path))
        assert result is not None

    def test_severity_levels_in_observations(self, tmp_path):
        """All severity levels should render without error."""
        obs = [
            {"finding": "Major issue", "location": "North", "severity": "major"},
            {"finding": "Moderate issue", "location": "South", "severity": "moderate"},
            {"finding": "Minor issue", "location": "East", "severity": "minor"},
            {"finding": "Info note", "location": "West", "severity": "info"},
        ]
        ai = {**SAMPLE_AI_ANALYSIS, "observations": obs}
        data = {**SAMPLE_DATA, "ai_analysis": ai, "images": SAMPLE_IMAGES}
        result = generate_report("roof_inspection", data, str(tmp_path))
        assert result is not None


@pytest.mark.skipif(not REPORTLAB_AVAILABLE, reason="reportlab not installed")
class TestImageEmbedding:
    """Test report generation with embedded images."""

    def test_report_with_photo_thumbs(self, tmp_path):
        # Create fake thumbnails
        from PIL import Image
        thumbs = []
        for i in range(4):
            thumb_path = tmp_path / f"photo_{i}_thumb.jpg"
            img = Image.new("RGB", (600, 450), color=(100 + i * 30, 80, 60))
            img.save(str(thumb_path), format="JPEG")
            thumbs.append((str(thumb_path), f"Nadir | -85° pitch | 120m alt"))

        images = {"photo_thumbs": thumbs, "ortho_preview": None, "dsm_preview": None}
        data = {**SAMPLE_DATA, "images": images}
        result = generate_report("construction_progress", data, str(tmp_path / "out"))
        assert result is not None
        assert os.path.getsize(result["pdf_path"]) > 5000

    def test_report_with_ortho_preview(self, tmp_path):
        from PIL import Image
        ortho_path = tmp_path / "ortho_preview.jpg"
        img = Image.new("RGB", (1200, 900), color=(50, 120, 50))
        img.save(str(ortho_path), format="JPEG")

        images = {"photo_thumbs": [], "ortho_preview": str(ortho_path), "dsm_preview": None}
        data = {**SAMPLE_DATA, "images": images}
        result = generate_report("property_survey", data, str(tmp_path / "out"))
        assert result is not None

    def test_report_with_all_images_and_ai(self, tmp_path):
        """Full report: AI + photos + ortho + DSM."""
        from PIL import Image

        thumbs = []
        for i in range(2):
            p = tmp_path / f"thumb_{i}.jpg"
            Image.new("RGB", (600, 450), (100, 100, 100)).save(str(p))
            thumbs.append((str(p), f"Photo {i+1}"))

        ortho = tmp_path / "ortho.jpg"
        Image.new("RGB", (1200, 900), (50, 120, 50)).save(str(ortho))

        dsm = tmp_path / "dsm.jpg"
        Image.new("RGB", (800, 600), (200, 100, 50)).save(str(dsm))

        images = {
            "photo_thumbs": thumbs,
            "ortho_preview": str(ortho),
            "dsm_preview": str(dsm),
        }
        data = {**SAMPLE_DATA, "ai_analysis": SAMPLE_AI_ANALYSIS, "images": images}
        result = generate_report("construction_progress", data, str(tmp_path / "full"))
        assert result is not None
        # Full report with images + AI should be substantial
        assert os.path.getsize(result["pdf_path"]) > 10000
