"""Tests for report_generator."""

import os
import pytest
from report_generator import generate_report, REPORT_TYPES, REPORTLAB_AVAILABLE


class TestReportTypes:
    def test_all_types_registered(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate",
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


@pytest.mark.skipif(not REPORTLAB_AVAILABLE, reason="reportlab not installed")
class TestGenerateReport:
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
        # Create a fake file to list in downloads
        fake_ortho = tmp_path / "orthophoto.tif"
        fake_ortho.write_bytes(b"x" * 10000)

        data = {**SAMPLE_DATA, "downloads": {"orthophoto.tif": str(fake_ortho)}}
        result = generate_report("construction_progress", data, str(tmp_path))
        assert result is not None

    def test_report_without_gps(self, tmp_path):
        data = {**SAMPLE_DATA, "gps_bounds": None}
        result = generate_report("property_survey", data, str(tmp_path))
        assert result is not None

    def test_all_six_types_produce_unique_pdfs(self, tmp_path):
        pdfs = []
        for report_type in REPORT_TYPES:
            data = {**SAMPLE_DATA, "job_type": report_type}
            result = generate_report(report_type, data, str(tmp_path))
            assert result is not None, f"{report_type} failed"
            pdfs.append(result["pdf_path"])

        # All files should be different (different filenames at minimum)
        assert len(set(pdfs)) == 6
