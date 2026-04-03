"""Tests for odm_presets job type definitions."""

import pytest
from odm_presets import PRESETS, get_preset, JOB_TYPES


class TestPresets:
    def test_all_seven_job_types_exist(self):
        expected = {
            "construction_progress", "property_survey", "roof_inspection",
            "structures", "vegetation", "real_estate", "gaussian_splat",
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
            if preset.get("engine") == "mipmap":
                continue  # MipMap presets don't use ODM options
            option_names = {o["name"] for o in preset["odm_options"]}
            assert "split" in option_names, f"{name} missing split option"
            assert "split-overlap" in option_names, f"{name} missing split-overlap"
            assert "sm-cluster" in option_names, f"{name} missing sm-cluster"

    def test_split_value_is_reasonable(self):
        for name, preset in PRESETS.items():
            if preset.get("engine") == "mipmap":
                continue
            split_opt = next(o for o in preset["odm_options"] if o["name"] == "split")
            assert 50 <= split_opt["value"] <= 500, (
                f"{name} split={split_opt['value']} should be 50-500 images per submodel"
            )

    def test_downloads_are_lists(self):
        for name, preset in PRESETS.items():
            assert isinstance(preset["downloads"], list)
            assert len(preset["downloads"]) > 0

    def test_odm_presets_download_orthophoto(self):
        for name, preset in PRESETS.items():
            if preset.get("engine") == "mipmap":
                continue
            assert "orthophoto.tif" in preset["downloads"], f"{name} should download orthophoto"

    def test_report_type_matches_key(self):
        for name, preset in PRESETS.items():
            assert preset["report_type"] == name


class TestGaussianSplatPreset:
    def test_gaussian_splat_in_job_types(self):
        assert ("gaussian_splat", "Gaussian Splat") in JOB_TYPES

    def test_engine_is_mipmap(self):
        assert PRESETS["gaussian_splat"]["engine"] == "mipmap"

    def test_no_odm_options(self):
        assert PRESETS["gaussian_splat"]["odm_options"] == []

    def test_photo_filter_none(self):
        assert PRESETS["gaussian_splat"]["photo_filter"] is None

    def test_downloads(self):
        assert PRESETS["gaussian_splat"]["downloads"] == ["gs_ply", "gs_sog_tiles"]

    def test_mipmap_settings(self):
        settings = PRESETS["gaussian_splat"]["mipmap_settings"]
        assert settings["resolution_level"] == 3
        assert settings["mesh_decimate_ratio"] == 0.5

    def test_report_type(self):
        assert PRESETS["gaussian_splat"]["report_type"] == "gaussian_splat"


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
