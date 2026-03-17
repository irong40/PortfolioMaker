"""Tests for odm_presets job type definitions."""

import pytest
from odm_presets import PRESETS, get_preset, JOB_TYPES


class TestPresets:
    def test_all_six_job_types_exist(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate",
        }
        assert set(PRESETS.keys()) == expected

    def test_preset_has_required_keys(self):
        required = {"label", "description", "photo_filter", "odm_options", "downloads", "report_type"}
        for name, preset in PRESETS.items():
            assert required.issubset(preset.keys()), f"{name} missing keys: {required - preset.keys()}"

    def test_photo_filter_valid_values(self):
        for name, preset in PRESETS.items():
            assert preset["photo_filter"] in ("nadir", None), (
                f"{name} photo_filter must be 'nadir' or None, got {preset['photo_filter']}"
            )

    def test_nadir_filter_for_mapping_types(self):
        assert PRESETS["construction_progress"]["photo_filter"] == "nadir"
        assert PRESETS["property_survey"]["photo_filter"] == "nadir"
        assert PRESETS["vegetation"]["photo_filter"] == "nadir"

    def test_all_photos_for_3d_types(self):
        assert PRESETS["roof_inspection"]["photo_filter"] is None
        assert PRESETS["structures"]["photo_filter"] is None
        assert PRESETS["real_estate"]["photo_filter"] is None

    def test_odm_options_include_split_merge(self):
        for name, preset in PRESETS.items():
            option_names = {o["name"] for o in preset["odm_options"]}
            assert "split" in option_names, f"{name} missing split option"
            assert "split-overlap" in option_names, f"{name} missing split-overlap"
            assert "sm-cluster" in option_names, f"{name} missing sm-cluster"

    def test_split_value_is_4(self):
        for name, preset in PRESETS.items():
            split_opt = next(o for o in preset["odm_options"] if o["name"] == "split")
            assert split_opt["value"] == 4, f"{name} split should be 4"

    def test_downloads_are_lists(self):
        for name, preset in PRESETS.items():
            assert isinstance(preset["downloads"], list)
            assert len(preset["downloads"]) > 0

    def test_all_presets_download_orthophoto(self):
        for name, preset in PRESETS.items():
            assert "orthophoto.tif" in preset["downloads"], f"{name} should download orthophoto"

    def test_report_type_matches_key(self):
        for name, preset in PRESETS.items():
            assert preset["report_type"] == name


class TestGetPreset:
    def test_returns_copy(self):
        p1 = get_preset("property_survey")
        p2 = get_preset("property_survey")
        assert p1 is not p2
        assert p1 == p2

    def test_mutation_does_not_affect_original(self):
        p = get_preset("construction_progress")
        p["label"] = "MUTATED"
        assert PRESETS["construction_progress"]["label"] == "Construction Progress"

    def test_invalid_raises_key_error(self):
        with pytest.raises(KeyError):
            get_preset("nonexistent")


class TestJobTypes:
    def test_length_matches_presets(self):
        assert len(JOB_TYPES) == len(PRESETS)

    def test_all_keys_in_presets(self):
        for key, label in JOB_TYPES:
            assert key in PRESETS, f"JOB_TYPES key '{key}' not in PRESETS"

    def test_labels_match_presets(self):
        for key, label in JOB_TYPES:
            assert PRESETS[key]["label"] == label
