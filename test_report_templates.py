"""Tests for report_templates — template structure and completeness."""

import pytest
from report_templates import TEMPLATES, get_template, ReportTemplate, ReportSection


class TestTemplateRegistry:
    def test_all_seven_types_registered(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate", "gaussian_splat",
        }
        assert set(TEMPLATES.keys()) == expected

    def test_get_template_returns_correct_type(self):
        t = get_template("roof_inspection")
        assert isinstance(t, ReportTemplate)
        assert t.report_type == "roof_inspection"
        assert "Roof" in t.title

    def test_get_template_returns_none_for_unknown(self):
        assert get_template("nonexistent") is None


class TestTemplateStructure:
    """Every template must have required fields and structural integrity."""

    @pytest.mark.parametrize("report_type", TEMPLATES.keys())
    def test_has_required_fields(self, report_type):
        t = TEMPLATES[report_type]
        assert t.report_type == report_type
        assert len(t.title) > 5
        assert len(t.ai_system_addendum) > 10
        assert len(t.ai_prompt) > 20
        assert isinstance(t.ai_schema, dict)
        assert len(t.sections) >= 5

    @pytest.mark.parametrize("report_type", TEMPLATES.keys())
    def test_has_core_sections(self, report_type):
        """Every template must include flight_summary, deliverables, methodology."""
        t = TEMPLATES[report_type]
        section_keys = {s.key for s in t.sections}
        assert "flight_summary" in section_keys
        assert "deliverables" in section_keys
        assert "methodology" in section_keys

    @pytest.mark.parametrize("report_type", TEMPLATES.keys())
    def test_sections_are_valid(self, report_type):
        t = TEMPLATES[report_type]
        for section in t.sections:
            assert isinstance(section, ReportSection)
            assert len(section.key) > 0
            assert len(section.title) > 0
            if section.table_format:
                assert section.table_format in ("findings", "checklist", "matrix")

    @pytest.mark.parametrize("report_type", TEMPLATES.keys())
    def test_ai_schema_has_observations(self, report_type):
        """Every template schema should include observations for the findings table."""
        t = TEMPLATES[report_type]
        assert "observations" in t.ai_schema

    @pytest.mark.parametrize("report_type", TEMPLATES.keys())
    def test_photo_strategy_valid(self, report_type):
        t = TEMPLATES[report_type]
        assert t.photo_strategy in ("nadir_heavy", "oblique_heavy", "balanced")
        assert t.max_ai_photos >= 4


class TestTemplateUniqueness:
    """Each template should have unique content, not just copies."""

    def test_unique_ai_prompts(self):
        prompts = [t.ai_prompt for t in TEMPLATES.values()]
        assert len(set(prompts)) == len(prompts), "Duplicate AI prompts found"

    def test_unique_titles(self):
        titles = [t.title for t in TEMPLATES.values()]
        assert len(set(titles)) == len(titles), "Duplicate titles found"

    def test_roof_inspection_has_damage_sections(self):
        t = get_template("roof_inspection")
        keys = {s.key for s in t.sections}
        assert "damage_findings" in keys
        assert "flashing_condition" in keys
        assert "drainage_assessment" in keys

    def test_construction_has_earthwork_sections(self):
        t = get_template("construction_progress")
        keys = {s.key for s in t.sections}
        assert "earthwork" in keys
        assert "foundations" in keys
        assert "safety_compliance" in keys

    def test_vegetation_has_canopy_sections(self):
        t = get_template("vegetation")
        keys = {s.key for s in t.sections}
        assert "canopy_health" in keys
        assert "invasive_species" in keys
        assert "decline_indicators" in keys

    def test_survey_has_boundary_sections(self):
        t = get_template("property_survey")
        keys = {s.key for s in t.sections}
        assert "boundaries" in keys
        assert "encroachments" in keys
        assert "terrain_analysis" in keys

    def test_real_estate_has_marketing_sections(self):
        t = get_template("real_estate")
        keys = {s.key for s in t.sections}
        assert "marketing_highlights" in keys
        assert "outdoor_features" in keys

    def test_gaussian_splat_has_model_sections(self):
        t = get_template("gaussian_splat")
        keys = {s.key for s in t.sections}
        assert "coverage_assessment" in keys
        assert "model_use_cases" in keys
        assert "processing_details" in keys

    def test_structures_has_defect_sections(self):
        t = get_template("structures")
        keys = {s.key for s in t.sections}
        assert "surface_condition" in keys
        assert "deformation" in keys
        assert "corrosion" in keys

    def test_oblique_heavy_for_inspections(self):
        """Roof and structural inspections should prefer oblique photos."""
        assert get_template("roof_inspection").photo_strategy == "oblique_heavy"
        assert get_template("structures").photo_strategy == "oblique_heavy"

    def test_nadir_heavy_for_surveys(self):
        """Surveys and construction should prefer nadir photos."""
        assert get_template("construction_progress").photo_strategy == "nadir_heavy"
        assert get_template("property_survey").photo_strategy == "nadir_heavy"
        assert get_template("vegetation").photo_strategy == "nadir_heavy"
