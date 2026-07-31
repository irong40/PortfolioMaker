"""Tests for gsd.py — measured ground sample distance.

Pure pytest. No Tk, no sys.exit, no network, no writes outside tmp_path, and
nothing here can reach sortie_settings.json.
"""

import json
import math

import pytest
from PIL import Image

import gsd
from odm_presets import PLATFORM_PROFILES, get_preset


# ─── FIXTURE HELPERS ────────────────────────────────────────────────────────

FC8482 = dict(model="FC8482", focal_mm=6.72, focal_equiv_mm=24.0)

# Focal length in pixels that two archived ODM/OpenSfM reconstructions solved
# for the same DJI FC8482 at 8064x4536. OpenSfM normalises focal by the larger
# image dimension, so f_px = focal_x * 8064.
#   E:\Portfolio\HamptonCemetery\all\cameras.json           focal_x 0.6924393
#   E:\Portfolio\TrainingPhotos\organized\ortho-output\...  focal_x 0.6740040
ODM_SOLVED_FOCAL_PX = (0.6924393224573189 * 8064, 0.6740040164757125 * 8064)


def geometry(width=8064, height=4536, zoom=1.0, **kwargs):
    geom = dict(FC8482)
    geom.update(kwargs)
    geom.update(width_px=width, height_px=height, zoom=zoom)
    return geom


def xmp(agl=61.2, pitch=-90.0):
    fields = {}
    if agl is not None:
        fields["RelativeAltitude"] = str(agl)
    if pitch is not None:
        fields["GimbalPitchDegree"] = str(pitch)
    return fields


def frame(name="DJI_0001.JPG", agl=61.2, pitch=-90.0, modes=None, **geom_kwargs):
    """Build one FrameGSD with zero file I/O."""
    return gsd.frame_gsd(name, pitch=pitch, xmp_fields=xmp(agl, pitch),
                         geometry=geometry(**geom_kwargs), modes=modes)


def nadir_frames(values_cm, model="FC8482", width=8064, f35=24.0):
    """Synthesise measured nadir frames at exact GSDs by solving for altitude."""
    out = []
    for i, target in enumerate(values_cm):
        spec = gsd.SENSOR_SPECS.get((model, int(f35)))
        sensor_w = spec["sensor_width_mm"] if spec else 9.677
        agl = gsd.altitude_for_target_gsd_m(
            target, focal_mm=6.72, width_px=width, sensor_width_mm=sensor_w)
        out.append(frame(name=f"F{i}.JPG", agl=agl, pitch=-90.0,
                         width=width, model=model, focal_equiv_mm=f35))
    return out


def write_jpeg(path, width=8064, height=4536, model="FC8482", focal=6.72,
               f35=24, zoom=1.0, agl=61.2, pitch=-90.0):
    """A tiny JPEG carrying the EXIF (and optionally XMP) a DJI frame carries."""
    img = Image.new("RGB", (32, 18), "white")
    exif = img.getexif()
    if model is not None:
        exif[0x0110] = model
    sub = exif.get_ifd(0x8769)
    if focal is not None:
        sub[0x920A] = focal
    if f35 is not None:
        sub[0xA405] = f35
    if width is not None:
        sub[0xA002] = width
        sub[0xA003] = height
    sub[0xA404] = zoom
    # format is forced so a ".dng" fixture can carry JPEG bytes — PIL sniffs
    # content on read, which is all read_camera_geometry needs.
    img.save(str(path), format="JPEG", exif=exif)
    if agl is not None or pitch is not None:
        # extract_xmp_fields byte-searches the file head for <x:xmpmeta, so an
        # appended block is found exactly as an embedded one would be.
        block = ('<x:xmpmeta xmlns:x="adobe:ns:meta/">'
                 f'<rdf:Description drone-dji:RelativeAltitude="{agl}" '
                 f'drone-dji:GimbalPitchDegree="{pitch}" '
                 f'drone-dji:GimbalRollDegree="0.0" '
                 f'drone-dji:GimbalYawDegree="0.0" '
                 f'drone-dji:AbsoluteAltitude="100.0"/>'
                 '</x:xmpmeta>')
        with open(str(path), "ab") as fh:
            fh.write(block.encode("utf-8"))
    return path


# ─── FORMULA ────────────────────────────────────────────────────────────────

class TestFormula:

    def test_mini4pro_at_61_2_metres(self):
        """Hand fixed point: 9.677 mm / 6.72 mm x 61.2 m / 8064 px."""
        f = frame(agl=61.2)
        assert f.status == gsd.STATUS_OK
        assert f.method == gsd.METHOD_SENSOR_TABLE
        assert f.gsd_cm == pytest.approx(1.0930, abs=0.001)

    def test_gsd_is_linear_in_altitude(self):
        """The dependent-variable claim, stated as a test."""
        a = frame(agl=30.0).gsd_cm
        b = frame(agl=60.0).gsd_cm
        assert b == pytest.approx(2 * a, rel=1e-9)

    def test_gsd_depends_on_capture_mode_not_altitude_alone(self):
        """Same aircraft, same altitude, two frame widths — two GSDs.

        This is the second variable: a 12 MP frame samples the ground twice
        as coarsely as a 48 MP frame from identical height, which is exactly
        what a single nominal per-platform constant could not express.
        """
        wide = frame(agl=61.2, width=8064, height=4536)
        half = frame(agl=61.2, width=4032, height=3024)
        assert wide.status == gsd.STATUS_OK and half.status == gsd.STATUS_OK
        assert half.gsd_cm == pytest.approx(2 * wide.gsd_cm, rel=1e-6)

    def test_deleted_nominal_constant_was_wrong_by_about_two_times(self):
        """odm_presets used to declare 2.18 cm/px at 200 ft for this aircraft."""
        at_200ft = frame(agl=60.96).gsd_cm
        assert at_200ft == pytest.approx(1.089, abs=0.01)
        assert 2.18 / at_200ft > 1.9

    def test_altitude_for_target_gsd_inverts_the_formula(self):
        agl = gsd.altitude_for_target_gsd_m(
            1.0, focal_mm=6.72, width_px=8064, sensor_width_mm=9.677)
        assert agl == pytest.approx(56.0, abs=0.5)
        assert frame(agl=agl).gsd_cm == pytest.approx(1.0, abs=1e-6)


class TestFormulaAgainstOdmGroundTruth:
    """The sensor table is calibration data, checked against real ODM output."""

    def _table_focal_px(self):
        spec = gsd.SENSOR_SPECS[("FC8482", 24)]
        return 6.72 / spec["sensor_width_mm"] * spec["native_px_width"]

    def test_sensor_table_agrees_with_both_archived_reconstructions(self):
        # ODM's own calibration scatters 2.7% between the two archived jobs on
        # this camera, so that is the noise floor for any agreement assertion.
        # A tighter bound would be a flaky test of a value we do not control.
        est = self._table_focal_px()
        for solved in ODM_SOLVED_FOCAL_PX:
            assert abs(est - solved) / solved < 0.035

    def test_sensor_table_beats_the_bare_36mm_form_on_average(self):
        table = self._table_focal_px()
        bare_36mm = 24.0 * 8064 / 36.0
        err_table = sum(abs(table - s) / s for s in ODM_SOLVED_FOCAL_PX)
        err_36 = sum(abs(bare_36mm - s) / s for s in ODM_SOLVED_FOCAL_PX)
        assert err_table < err_36

    def test_captured_aspect_diagonal_form_is_the_trap_we_avoid(self):
        """Regression guard: DJI keeps f35=24 on a 16:9 crop of a 4:3 sensor.

        Splitting the 35mm diagonal at the CAPTURED aspect therefore runs
        ~8% low. If anyone reintroduces that form, this fails.
        """
        diag_mm = gsd.FRAME_35MM_DIAG_MM * 6.72 / 24.0
        sensor_w = diag_mm / math.hypot(1.0, 4536 / 8064)
        f_px = 6.72 / sensor_w * 8064
        assert (ODM_SOLVED_FOCAL_PX[0] - f_px) / ODM_SOLVED_FOCAL_PX[0] > 0.05

    def test_sensor_table_only_carries_verified_entries(self):
        for key, spec in gsd.SENSOR_SPECS.items():
            assert spec["source"], f"{key} has no provenance"
            assert "cameras.json" in spec["source"], (
                f"{key} was not checked against an ODM reconstruction")


class TestAspectImmunity:

    def test_sixteen_by_nine_crop_gives_the_same_gsd_as_four_by_three(self):
        """The in-camera 4:3 -> 16:9 crop changes the frame, not the sampling."""
        wide = frame(width=8064, height=4536)
        native = frame(width=8064, height=6048)
        assert wide.gsd_cm == pytest.approx(native.gsd_cm, rel=1e-9)

    def test_unknown_camera_falls_back_and_says_so(self):
        f = frame(model="XX9999", focal_mm=12.29, focal_equiv_mm=24.0,
                  width=5280, height=3956)
        assert f.status == gsd.STATUS_OK
        assert f.method == gsd.METHOD_EXIF_DERIVED
        assert gsd.FLAG_ASSUMED_NATIVE_ASPECT in f.flags
        assert f.uncertainty_pct == gsd.UNCERTAINTY_EXIF_DERIVED_PCT

    def test_cropped_aspect_on_an_unknown_camera_widens_uncertainty(self):
        f = frame(model="XX9999", width=8064, height=4536)
        assert gsd.FLAG_CROPPED_ASPECT_SUSPECTED in f.flags
        assert f.uncertainty_pct == gsd.UNCERTAINTY_CROPPED_ASPECT_PCT


class TestDigitalZoom:

    def test_zoom_shrinks_output_pixels_but_not_real_samples(self):
        """Digital zoom does not improve ground sampling — only gsd_optical
        may be claimed, or the report sells interpolated pixels as detail."""
        nominal = frame(zoom=1.0)
        zoomed = frame(zoom=1.87)
        assert zoomed.gsd_output_cm == pytest.approx(
            nominal.gsd_optical_cm / 1.87, rel=1e-6)
        assert zoomed.gsd_optical_cm == pytest.approx(
            nominal.gsd_optical_cm, rel=1e-6)
        assert zoomed.gsd_cm == zoomed.gsd_optical_cm
        assert gsd.FLAG_DIGITAL_ZOOM in zoomed.flags

    def test_unzoomed_frames_are_not_flagged(self):
        assert gsd.FLAG_DIGITAL_ZOOM not in frame(zoom=1.0).flags


class TestObliques:

    def test_oblique_gets_no_scalar_gsd(self):
        f = frame(pitch=-45.0)
        assert f.status == gsd.STATUS_OK
        assert f.geometry == gsd.GEOMETRY_OBLIQUE
        assert f.gsd_cm is None

    def test_oblique_band_is_ordered_near_to_far(self):
        f = frame(pitch=-45.0)
        assert f.gsd_near_cm < f.gsd_center_cm < f.gsd_far_cm

    def test_far_edge_is_none_when_the_frame_reaches_the_horizon(self):
        f = frame(pitch=-20.0)
        assert f.half_fov_deg > 20.0
        assert f.gsd_far_cm is None
        assert f.gsd_near_cm is not None

    def test_metrology_gate_is_stricter_than_the_sorting_threshold(self):
        """-75 sorts as nadir but is not a metrology frame."""
        assert gsd.NADIR_METROLOGY_PITCH_DEG > 70.0
        assert frame(pitch=-75.0).geometry == gsd.GEOMETRY_OBLIQUE
        assert frame(pitch=-85.0).geometry == gsd.GEOMETRY_NADIR

    def test_obliques_never_reach_a_mission_statistic(self):
        summary = gsd.summarize([frame(name=f"o{i}.JPG", pitch=-45.0)
                                 for i in range(30)])
        assert summary.measured == 0
        assert summary.oblique_excluded == 30
        assert summary.median_cm is None
        assert summary.sufficient is False


# ─── DEGRADATION ────────────────────────────────────────────────────────────

class TestUnmeasurable:

    def test_every_failure_path_yields_a_reason_and_no_number(self):
        cases = [
            (gsd.REASON_UNREADABLE, gsd.frame_gsd("x.JPG", geometry=None)),
            (gsd.REASON_NO_CAMERA_EXIF,
             gsd.frame_gsd("x.tif", geometry={"model": None, "focal_mm": None,
                                              "focal_equiv_mm": None,
                                              "width_px": 900, "height_px": 600,
                                              "zoom": 1.0})),
            (gsd.REASON_NO_PIXEL_DIMENSIONS,
             frame(width=None, height=None)),
            (gsd.REASON_COMPOSITE_IMAGE, frame(width=12000, height=6000)),
            (gsd.REASON_NO_AGL, frame(agl=None)),
            (gsd.REASON_AGL_BELOW_FLOOR, frame(agl=1.0)),
            (gsd.REASON_NO_GIMBAL_PITCH, frame(pitch=None)),
        ]
        for expected, f in cases:
            assert f.reason == expected, f"expected {expected}, got {f.reason}"
            assert f.status == gsd.STATUS_UNMEASURABLE
            assert f.gsd_cm is None
            assert f.gsd_optical_cm is None
            assert expected in gsd.REASON_TEXT

    def test_focal_equivalent_missing_on_an_unknown_camera(self):
        f = frame(model="XX9999", focal_equiv_mm=None)
        assert f.reason == gsd.REASON_NO_FOCAL_EQUIV
        assert f.gsd_cm is None

    def test_altitude_exactly_zero_is_not_an_infinitely_sharp_measurement(self):
        """sentinel_core.extract_xmp_gimbal turns an ABSENT RelativeAltitude
        into 0.0. Consumed naively that divides into a 0.00 cm/px claim."""
        f = frame(agl=0.0)
        assert f.reason == gsd.REASON_AGL_BELOW_FLOOR
        assert f.status == gsd.STATUS_UNMEASURABLE
        assert f.gsd_cm is None

    def test_absent_altitude_field_is_distinguished_from_a_tiny_one(self):
        assert frame(agl=None).reason == gsd.REASON_NO_AGL
        assert frame(agl=0.5).reason == gsd.REASON_AGL_BELOW_FLOOR

    def test_negative_or_zero_focal_length_does_not_divide_by_zero(self):
        assert frame(focal_mm=0.0).gsd_cm is None

    def test_dng_preview_dimensions_are_never_used(self, tmp_path):
        """A DJI DNG has no EXIF PixelXDimension and PIL reports its 160x90
        embedded preview. Trusting im.size yields ~57 cm/px on half a card."""
        p = write_jpeg(tmp_path / "raw.jpg", width=None, height=None)
        geom = gsd.read_camera_geometry(str(p))
        assert geom["width_px"] is None
        assert gsd.frame_gsd(str(p)).reason == gsd.REASON_NO_PIXEL_DIMENSIONS

    def test_unreadable_file_does_not_raise(self, tmp_path):
        p = tmp_path / "broken.jpg"
        p.write_bytes(b"not a jpeg")
        assert gsd.read_camera_geometry(str(p)) is None
        assert gsd.frame_gsd(str(p)).reason == gsd.REASON_UNREADABLE


class TestCompositeDetection:

    def test_modal_geometry_flags_the_odd_frame_out(self):
        geoms = [geometry() for _ in range(8)] + [geometry(12000, 6000)]
        modes = gsd.modal_geometry(geoms)
        assert modes[("FC8482", 24)] == (8064, 4536)
        assert gsd.is_composite(geometry(12000, 6000), modes) is True
        assert gsd.is_composite(geometry(), modes) is False

    def test_modal_geometry_needs_enough_frames_to_establish_a_mode(self):
        geoms = [geometry() for _ in range(gsd.MIN_FAMILY_FRAMES_FOR_MODE - 1)]
        assert gsd.modal_geometry(geoms) == {}

    def test_fallback_threshold_does_not_eat_real_16_by_9_frames(self):
        """8064/4536 = 1.7778. The repo's existing 1.8 panorama cut sits only
        1.25% above a legitimate frame, which is why this is 1.90."""
        assert gsd.FALLBACK_COMPOSITE_ASPECT > 1.8
        assert gsd.is_composite(geometry(8064, 4536), None) is False
        assert gsd.is_composite(geometry(12000, 6000), None) is True

    def test_composite_never_produces_a_number(self):
        f = frame(width=14400, height=7200)
        assert f.reason == gsd.REASON_COMPOSITE_IMAGE
        assert f.gsd_cm is None


class TestRawSidecarPairing:

    def test_raw_with_a_measured_sibling_leaves_both_sides_of_coverage(self):
        jpg = frame(name=r"C:\card\DJI_0001.JPG")
        dng = gsd.frame_gsd(r"C:\card\DJI_0001.DNG",
                            geometry=geometry(width=None, height=None),
                            xmp_fields=xmp())
        paired = gsd.pair_raw_sidecars([jpg, dng])
        assert paired[1].status == gsd.STATUS_EXCLUDED
        assert paired[1].reason == gsd.REASON_RAW_SIDECAR
        assert paired[1].gsd_cm is None

        summary = gsd.summarize(paired)
        assert summary.considered == 1
        assert summary.measured == 1
        assert summary.coverage == 1.0

    def test_orphan_raw_stays_an_honest_failure(self):
        dng = gsd.frame_gsd(r"C:\card\ONLY_RAW.DNG",
                            geometry=geometry(width=None, height=None),
                            xmp_fields=xmp())
        paired = gsd.pair_raw_sidecars([dng])
        assert paired[0].status == gsd.STATUS_UNMEASURABLE
        assert paired[0].reason == gsd.REASON_NO_PIXEL_DIMENSIONS

    def test_pairing_never_infers_geometry_across_files(self):
        jpg = frame(name=r"C:\card\DJI_0001.JPG")
        dng = gsd.frame_gsd(r"C:\card\DJI_0001.DNG",
                            geometry=geometry(width=None, height=None),
                            xmp_fields=xmp())
        assert gsd.pair_raw_sidecars([jpg, dng])[1].gsd_cm is None


# ─── SUMMARY ────────────────────────────────────────────────────────────────

class TestSummarize:

    def test_empty_input_is_a_valid_refusal(self):
        s = gsd.summarize([])
        assert s.sufficient is False
        assert s.resolution_tier == gsd.TIER_UNCLASSIFIED
        assert s.consistency_class == gsd.CONSISTENCY_UNKNOWN
        assert s.median_cm is None
        assert "not measured" in s.headline()

    def test_all_unmeasurable_reports_the_dominant_reason(self):
        s = gsd.summarize([frame(name=f"t{i}.tif", agl=None) for i in range(7)])
        assert s.sufficient is False
        assert s.dominant_reason == gsd.REASON_NO_AGL
        assert "no relative altitude" in s.headline()

    def test_uniform_grid(self):
        s = gsd.summarize(nadir_frames([1.09] * 40))
        assert s.measured == 40
        assert s.sufficient is True
        assert s.consistency_class == gsd.CONSISTENCY_UNIFORM
        assert s.uniformity_ratio == pytest.approx(1.0, abs=1e-6)
        assert s.resolution_tier == "T2_survey"

    def test_stratified_capture_is_named_and_warned_about(self):
        s = gsd.summarize(nadir_frames([1.0] * 20 + [3.0] * 20))
        assert s.consistency_class == gsd.CONSISTENCY_STRATIFIED
        assert any("stratified" in w for w in s.warnings)

    def test_tier_boundaries(self):
        assert gsd.tier_for(0.99, 40) == "T1_inspection"
        assert gsd.tier_for(1.0, 40) == "T2_survey"
        assert gsd.tier_for(2.0, 40) == "T3_mapping"
        assert gsd.tier_for(4.0, 40) == "T4_overview"
        assert gsd.tier_for(10.0, 40) == gsd.TIER_COARSEST
        assert gsd.tier_for(1.0, 4) == gsd.TIER_UNCLASSIFIED
        assert gsd.tier_for(None, 400) == gsd.TIER_UNCLASSIFIED

    def test_tier_uses_p95_not_median(self):
        """The mosaic is resampled onto one grid, so the honest bound comes
        from the coarsest contributing frames."""
        s = gsd.summarize(nadir_frames([0.5] * 38 + [3.0] * 12))
        assert s.median_cm < 1.0
        assert s.resolution_tier != "T1_inspection"

    def test_too_few_frames_permits_no_claim(self):
        s = gsd.summarize(nadir_frames([1.09] * 4))
        assert s.measured == 4
        assert s.sufficient is False
        assert any("need 5" in w for w in s.warnings)

    def test_low_coverage_permits_no_claim(self):
        good = nadir_frames([1.09] * 6)
        bad = [frame(name=f"b{i}.tif", agl=None) for i in range(40)]
        s = gsd.summarize(good + bad)
        assert s.measured == 6
        assert s.coverage < gsd.MIN_COVERAGE
        assert s.sufficient is False
        assert any("coverage" in w for w in s.warnings)

    def test_multi_sensor_is_flagged_even_when_the_spread_is_tight(self):
        s = gsd.summarize(nadir_frames([1.09] * 20)
                          + nadir_frames([1.10] * 20, model="XX9999",
                                         width=5280))
        assert len(s.capture_modes) == 2
        assert any("multi_sensor" in w for w in s.warnings)

    def test_exif_fallback_is_surfaced_as_a_warning(self):
        s = gsd.summarize(nadir_frames([1.2] * 30, model="XX9999", width=5280))
        assert s.method_mix == {gsd.METHOD_EXIF_DERIVED: 30}
        assert any("EXIF-derived" in w for w in s.warnings)

    def test_uncertainty_is_the_worst_case_present(self):
        s = gsd.summarize(nadir_frames([1.09] * 20))
        assert s.uncertainty_pct == gsd.UNCERTAINTY_SENSOR_TABLE_PCT
        assert "±" in s.headline() or "\u00b1" in s.headline()

    def test_coverage_never_travels_without_its_reason_histogram(self):
        """TanRd legitimately reads ~50% because half the card is RAW. Without
        the reasons beside it, that reads as a bug."""
        s = gsd.summarize(nadir_frames([1.09] * 10)
                          + [frame(name=f"r{i}.DNG", width=None, height=None)
                             for i in range(10)])
        line = s.coverage_line()
        assert "10 of 20" in line
        assert "RAW" in line

    def test_summarize_photos_reads_the_list_it_is_given(self):
        class P:
            def __init__(self, f):
                self.gsd = f

        all_photos = [P(f) for f in nadir_frames([1.09] * 20)]
        subset = all_photos[:6]
        assert gsd.summarize_photos(all_photos).measured == 20
        assert gsd.summarize_photos(subset).measured == 6

    def test_summarize_photos_tolerates_photos_without_gsd(self):
        class P:
            gsd = None

        assert gsd.summarize_photos([P(), P()]).measured == 0

    def test_as_dict_is_json_serialisable(self):
        s = gsd.summarize(nadir_frames([1.09] * 20) + [frame(agl=None)])
        text = json.dumps(s.as_dict())
        back = json.loads(text)
        assert back["measured"] == 20
        assert back["basis"] == "predicted_from_exif"
        assert back["resolution_tier"] == "T2_survey"

    def test_frame_as_dict_is_json_serialisable(self):
        json.dumps(frame().as_dict())
        json.dumps(frame(pitch=-45.0).as_dict())
        json.dumps(frame(agl=None).as_dict())


# ─── PRESET INTEGRATION ─────────────────────────────────────────────────────

class TestPresetIntegration:

    def test_conflict_gate_ends_with_a_re_fly_altitude(self):
        s = gsd.summarize(nadir_frames([2.4] * 40))
        msg = gsd.preset_resolution_conflict(s, get_preset("pavement"))
        assert msg is not None
        assert "1 cm/px" in msg
        assert "2.4" in msg
        assert "Re-fly at or below" in msg
        assert "m AGL" in msg

    def test_no_conflict_when_the_capture_supports_the_preset(self):
        s = gsd.summarize(nadir_frames([0.6] * 40))
        assert gsd.preset_resolution_conflict(s, get_preset("pavement")) is None

    def test_no_conflict_claim_without_a_measurement(self):
        assert gsd.preset_resolution_conflict(
            gsd.summarize([]), get_preset("pavement")) is None

    def test_coarsen_only_never_asks_for_finer_than_measured(self):
        preset = get_preset("pavement")
        s = gsd.summarize(nadir_frames([2.4] * 40))
        value, why = gsd.apply_measured_resolution(preset, s)
        assert value == pytest.approx(2.4, abs=0.05)
        assert value >= s.p95_cm
        assert gsd.orthophoto_resolution_of(preset) == value
        assert "coarsened" in why

    def test_coarsen_only_never_refines(self):
        """Throwing away real pixels is a product decision, not a defect —
        so the automatic path only ever moves in the safe direction."""
        preset = get_preset("construction_progress")
        before = gsd.orthophoto_resolution_of(preset)
        s = gsd.summarize(nadir_frames([1.09] * 40))
        value, why = gsd.apply_measured_resolution(preset, s)
        assert value is None
        assert gsd.orthophoto_resolution_of(preset) == before

    def test_coarsen_respects_the_predicted_bias_margin(self):
        """HamptonCemetery predicted 1.09 against a delivered 1.00 raster and
        an ODM-reported 0.96. Predicted runs coarse over canopy, so a 9%
        shortfall must NOT rewrite a 1 cm preset."""
        preset = get_preset("pavement")
        s = gsd.summarize(nadir_frames([1.09] * 40))
        value, _ = gsd.apply_measured_resolution(preset, s)
        assert value is None
        assert gsd.orthophoto_resolution_of(preset) == 1

    def test_coarsen_refuses_below_the_frame_count_gate(self):
        preset = get_preset("pavement")
        s = gsd.summarize(nadir_frames([2.4] * 10))
        value, why = gsd.apply_measured_resolution(preset, s)
        assert value is None
        assert "need 20" in why
        assert gsd.orthophoto_resolution_of(preset) == 1

    def test_coarsen_refuses_when_nothing_was_measured(self):
        preset = get_preset("pavement")
        value, why = gsd.apply_measured_resolution(preset, gsd.summarize([]))
        assert value is None
        assert "unmeasured" in why
        assert gsd.orthophoto_resolution_of(preset) == 1

    def test_coarsen_is_a_no_op_for_presets_without_an_ortho(self):
        preset = get_preset("gaussian_splat")
        value, why = gsd.apply_measured_resolution(
            preset, gsd.summarize(nadir_frames([2.4] * 40)))
        assert value is None
        assert "no orthophoto-resolution" in why

    def test_recommendation_is_advisory_and_mutates_nothing(self):
        preset = get_preset("construction_progress")
        before = json.dumps(preset["odm_options"], sort_keys=True)
        s = gsd.summarize(nadir_frames([1.09] * 40))
        value, why = gsd.recommended_orthophoto_resolution(s, preset)
        assert value == pytest.approx(1.5, abs=0.01)
        assert "discarded" in why
        assert json.dumps(preset["odm_options"], sort_keys=True) == before

    def test_recommendation_refuses_on_a_stratified_capture(self):
        s = gsd.summarize(nadir_frames([1.0] * 20 + [3.0] * 20))
        value, why = gsd.recommended_orthophoto_resolution(
            s, get_preset("property_survey"))
        assert value is None
        assert "stratified" in why

    def test_no_preset_carries_a_hardcoded_gsd_constant(self):
        for name, profile in PLATFORM_PROFILES.items():
            for key in profile:
                assert "gsd" not in key.lower(), (
                    f"{name} reintroduced a nominal GSD constant: {key}")


# ─── DELIVERED RASTER ───────────────────────────────────────────────────────

class TestAchievedFromRaster:

    def _write_tif(self, path, crs, pixel_m):
        rasterio = pytest.importorskip("rasterio")
        from rasterio.transform import from_origin
        import numpy as np
        with rasterio.open(
                str(path), "w", driver="GTiff", height=4, width=4, count=1,
                dtype="uint8", crs=crs,
                transform=from_origin(0, 0, pixel_m, pixel_m)) as dst:
            dst.write(np.zeros((4, 4), dtype="uint8"), 1)
        return path

    def test_reads_pixel_size_from_a_projected_raster(self, tmp_path):
        p = self._write_tif(tmp_path / "ortho.tif", "EPSG:32618", 0.02)
        out = gsd.achieved_gsd_from_raster(str(p))
        assert out["gsd_cm"] == pytest.approx(2.0, abs=1e-6)
        assert out["basis"] == "delivered_orthophoto"
        assert out["width"] == 4

    def test_geographic_crs_is_refused_not_converted(self, tmp_path):
        p = self._write_tif(tmp_path / "geo.tif", "EPSG:4326", 0.0000002)
        assert gsd.achieved_gsd_from_raster(str(p)) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert gsd.achieved_gsd_from_raster(str(tmp_path / "nope.tif")) is None
        assert gsd.achieved_gsd_from_raster(None) is None

    def test_non_raster_returns_none(self, tmp_path):
        p = tmp_path / "junk.tif"
        p.write_bytes(b"not a tif")
        assert gsd.achieved_gsd_from_raster(str(p)) is None


# ─── SURFACES ───────────────────────────────────────────────────────────────

class TestSurfaces:

    def test_delivered_raster_outranks_a_predicted_number(self):
        s = gsd.summarize(nadir_frames([1.09] * 40))
        achieved = {"gsd_cm": 2.0, "source": "orthophoto.tif"}
        row = gsd.report_resolution_row(s, achieved)
        assert row[0] == "Orthomosaic Resolution"
        assert "2.00 cm/pixel" in row[1]
        assert "1.09" not in row[1]

    def test_predicted_row_carries_its_qualifier_inseparably(self):
        s = gsd.summarize(nadir_frames([1.09] * 40))
        row = gsd.report_resolution_row(s, None)
        assert row[0] == "Predicted Ground Sample Distance"
        assert "predicted from flight metadata" in row[1]
        assert "not measured on a delivered raster" in row[1]

    def test_unmeasured_row_says_so(self):
        row = gsd.report_resolution_row(gsd.summarize([]), None)
        assert row == ["Ground Sample Distance", "Not measured"]

    def test_no_surface_makes_an_accuracy_claim(self):
        """Ties the open integrity item: this number may not be routed into
        survey-grade or accuracy-checkpoint language."""
        s = gsd.summarize(nadir_frames([1.09] * 40))
        blobs = [
            s.headline(), s.coverage_line(),
            " ".join(gsd.summary_lines(s, preset=get_preset("pavement"))),
            " ".join(gsd.report_resolution_row(s, None)),
            gsd.methodology_sentence(s, None) or "",
            gsd.methodology_sentence(None, {"gsd_cm": 2.0}) or "",
            json.dumps(s.as_dict()),
        ]
        for text in blobs:
            low = text.lower()
            # The phrase is allowed only inside the sentence that forbids it.
            if "survey-grade" in low or "accuracy-checkpoint" in low:
                assert "does not support survey-grade" in low, text
            assert "survey grade" not in low
            assert "accuracy checkpoint" not in low
            assert "survey-grade accuracy" not in low

    def test_a_stated_number_always_carries_the_accuracy_bar(self):
        s = gsd.summarize(nadir_frames([1.09] * 40))
        assert "does not support survey-grade" in gsd.methodology_sentence(s, None)
        assert "does not support survey-grade" in gsd.methodology_sentence(
            None, {"gsd_cm": 2.0})

    def test_methodology_states_the_basis_actually_used(self):
        s = gsd.summarize(nadir_frames([1.09] * 40))
        assert "rather than measured on a delivered raster" in gsd.methodology_sentence(s, None)
        assert "delivered" in gsd.methodology_sentence(s, {"gsd_cm": 2.0})
        assert gsd.methodology_sentence(gsd.summarize([]), None) is None

    def test_coarse_missions_are_told_they_are_not_measurement_products(self):
        s = gsd.summarize(nadir_frames([6.0] * 40))
        assert s.resolution_tier == "T4_overview"
        assert "not a measurement product" in gsd.methodology_sentence(s, None)

    def test_summary_lines_refuse_rather_than_go_blank(self):
        lines = gsd.summary_lines(gsd.summarize(
            [frame(name=f"t{i}.tif", agl=None) for i in range(7)]))
        assert any("not measured" in line for line in lines)

    def test_accuracy_bar_is_stated_on_the_serialised_record(self):
        s = gsd.summarize(nadir_frames([1.09] * 40))
        assert "ground control" in s.as_dict()["accuracy_note"]


# ─── END TO END THROUGH photo_classifier ────────────────────────────────────

class TestClassifierIntegration:

    def test_classify_photos_attaches_gsd_and_manifest_carries_it(self, tmp_path):
        import photo_classifier

        for i in range(8):
            write_jpeg(tmp_path / f"DJI_{i:04d}.JPG", agl=61.2, pitch=-90.0)
        # A RAW sibling with no frame size, and a stitched composite.
        write_jpeg(tmp_path / "DJI_0000.dng", width=None, height=None)
        write_jpeg(tmp_path / "PANO.JPG", width=12000, height=6000)

        result = photo_classifier.classify_photos(str(tmp_path))
        measured = [p for p in result.photos if p.gsd_status == gsd.STATUS_OK]
        assert len(measured) == 8
        assert all(p.gsd_cm == pytest.approx(1.093, abs=0.01) for p in measured)
        assert all(p.gsd_method == gsd.METHOD_SENSOR_TABLE for p in measured)

        by_name = {p.filename: p for p in result.photos}
        assert by_name["DJI_0000.dng"].gsd_status == gsd.STATUS_EXCLUDED
        assert by_name["PANO.JPG"].gsd_reason == gsd.REASON_COMPOSITE_IMAGE

        out = tmp_path / "manifest.json"
        photo_classifier.write_manifest(result, out)
        manifest = json.loads(out.read_text())
        assert manifest["gsd"]["measured"] == 8
        assert manifest["gsd"]["resolution_tier"] == "T2_survey"
        assert manifest["gsd"]["sufficient"] is True
        rows = {row["filename"]: row for row in manifest["photos"]}
        assert rows["DJI_0003.JPG"]["gsd_cm"] == pytest.approx(1.093, abs=0.01)
        assert rows["DJI_0003.JPG"]["gsd_status"] == gsd.STATUS_OK
        assert rows["DJI_0003.JPG"]["gsd_geometry"] == gsd.GEOMETRY_NADIR
        assert rows["PANO.JPG"]["gsd_cm"] is None
        assert rows["PANO.JPG"]["gsd_reason"] == gsd.REASON_COMPOSITE_IMAGE

    def test_classification_result_holds_no_stored_summary(self):
        """The mission number is a function of a photo list, never a stored
        field — otherwise a filtered working set silently disagrees with it."""
        from photo_classifier import ClassificationResult
        assert not hasattr(ClassificationResult(source_dir="/x"), "gsd")

    def test_filtered_working_set_reports_its_own_gsd(self, tmp_path):
        import photo_classifier

        for i in range(6):
            write_jpeg(tmp_path / f"N_{i}.JPG", agl=61.2, pitch=-90.0)
        for i in range(6):
            write_jpeg(tmp_path / f"O_{i}.JPG", agl=30.0, pitch=-20.0)

        result = photo_classifier.classify_photos(str(tmp_path))
        working = photo_classifier.filter_photos(result, classification="nadir")
        whole = gsd.summarize_photos(result.photos)
        subset = gsd.summarize_photos(working.photos)
        assert whole.considered == 12
        assert subset.considered == 6
        assert subset.coverage == 1.0
        assert subset.sufficient is True

    def test_empty_folder_does_not_crash(self, tmp_path):
        import photo_classifier
        result = photo_classifier.classify_photos(str(tmp_path))
        assert gsd.summarize_photos(result.photos).sufficient is False


# ─── DOWNSTREAM SURFACES ────────────────────────────────────────────────────

class TestReportGeneratorRow:

    def test_delivered_raster_wins_the_resolution_row(self):
        from report_generator import _resolution_row
        row = _resolution_row({
            "gsd_achieved": {"gsd_cm": 2.0},
            "gsd_predicted": gsd.summarize(nadir_frames([1.09] * 40)).as_dict(),
        })
        assert row[0] == "Orthomosaic Resolution"
        assert "2.00 cm/pixel" in row[1]

    def test_predicted_row_is_labelled_predicted(self):
        from report_generator import _resolution_row
        row = _resolution_row({
            "gsd_achieved": None,
            "gsd_predicted": gsd.summarize(nadir_frames([1.09] * 40)).as_dict(),
        })
        assert row[0] == "Predicted Ground Sample Distance"
        assert "not measured on a delivered raster" in row[1]

    def test_insufficient_measurement_is_never_dressed_up(self):
        from report_generator import _resolution_row
        assert _resolution_row({}) == ["Ground Sample Distance", "Not measured"]
        assert _resolution_row({
            "gsd_predicted": gsd.summarize(nadir_frames([1.09] * 3)).as_dict(),
        }) == ["Ground Sample Distance", "Not measured"]

    def test_methodology_sentence_is_omitted_when_nothing_was_measured(self):
        from report_generator import _resolution_basis_sentence
        assert _resolution_basis_sentence({}) is None
        assert "ground control" in _resolution_basis_sentence(
            {"gsd_achieved": {"gsd_cm": 2.0}})


class TestCrmPayload:

    def _payload(self, report_data):
        import crm_sync

        class Mission:
            client_name = "C"
            client_company = ""
            address = city = state = ""
            job_number = "J1"

            def suggested_site_name(self):
                return "Site"

        section_data, _, _ = crm_sync.build_report_payload(
            Mission(), {"report_data": report_data}, "Survey")
        return section_data["flight_data"]

    def test_absent_measurement_emits_no_gsd_key_at_all(self):
        flight = self._payload({"total_photos": 10})
        assert "gsd_cm" not in flight
        assert "gsd_basis" not in flight

    def test_delivered_basis_is_recorded(self):
        flight = self._payload({"total_photos": 10,
                                "gsd_achieved": {"gsd_cm": 2.004}})
        assert flight["gsd_cm"] == 2.0
        assert flight["gsd_basis"] == "delivered_orthophoto"

    def test_predicted_basis_carries_its_uncertainty(self):
        summary = gsd.summarize(nadir_frames([1.09] * 40))
        flight = self._payload({"total_photos": 40,
                                "gsd_predicted": summary.as_dict()})
        assert flight["gsd_basis"] == "predicted_from_exif"
        assert flight["gsd_uncertainty_pct"] == gsd.UNCERTAINTY_SENSOR_TABLE_PCT
        assert flight["gsd_frames_measured"] == 40

    def test_insufficient_prediction_is_not_published(self):
        summary = gsd.summarize(nadir_frames([1.09] * 3))
        flight = self._payload({"total_photos": 3,
                                "gsd_predicted": summary.as_dict()})
        assert "gsd_cm" not in flight


class TestSortieScanSummary:

    def test_clause_is_appended_only_when_a_claim_is_permitted(self, tmp_path):
        import photo_classifier
        import sortie

        for i in range(8):
            write_jpeg(tmp_path / f"N_{i}.JPG", agl=61.2, pitch=-90.0)
        result = photo_classifier.classify_photos(str(tmp_path))
        working = photo_classifier.filter_photos(result, classification="nadir")

        text = sortie.scan_summary(result, working, get_preset("property_survey"))
        assert "8 nadir photos" in text
        assert "1.09 cm/px measured" in text
        assert "uniform" in text

    def test_no_clause_when_nothing_is_measurable(self):
        from photo_classifier import ClassificationResult
        import sortie

        empty = ClassificationResult(source_dir="/card", total=40)
        text = sortie.scan_summary(empty, empty, get_preset("property_survey"))
        assert "40" in text
        assert "cm/px" not in text


# ─── REGRESSION: cross-check findings, 2026-07-30 ────────────────────────────
#
# Each test below fails on the pre-fix code. Values come from adversarial
# verification against Adam's real folders, not from invention.

class TestRecommendedAltitudePercentileMismatch:
    """recommended_agl_m paired agl_max (p100) with p95_cm (p95).

    Every pre-existing test used a FLAT distribution, where agl_max == agl_p95
    and the error cancels to exactly zero. Only a spread exposes it, and the
    error is in the PERMISSIVE direction: the operator re-flies too high.
    """

    def test_recommendation_is_not_inflated_by_a_spread(self):
        # A spread of real GSDs. Coarsest frame is 1.80 cm/px.
        frames = nadir_frames([1.00, 1.15, 1.30, 1.45, 1.60, 1.80] * 8)
        summary = gsd.summarize(frames)
        assert summary.sufficient

        rec = gsd.recommended_agl_m(summary, 1.00)
        # Correct: scale the highest altitude by target/coarsest — both p100.
        expected = summary.agl_max_m * (1.00 / summary.max_cm)
        assert rec == pytest.approx(expected, rel=1e-9)

        # And the recommendation must actually achieve the target, not overshoot.
        assert rec <= summary.agl_max_m * (1.00 / summary.p95_cm) + 1e-9

    def test_flying_the_recommendation_hits_the_target(self):
        frames = nadir_frames([1.00, 1.20, 1.40, 1.60, 1.80] * 8)
        summary = gsd.summarize(frames)
        rec = gsd.recommended_agl_m(summary, 1.00)
        # GSD is linear in AGL, so the coarsest frame re-flown at `rec`
        # must land at or below the 1.00 target.
        achieved_worst = summary.max_cm * (rec / summary.agl_max_m)
        assert achieved_worst <= 1.00 + 1e-9

    def test_flat_distribution_still_agrees(self):
        # Guard the old behaviour where it was already correct.
        frames = nadir_frames([2.4] * 40)
        summary = gsd.summarize(frames)
        rec = gsd.recommended_agl_m(summary, 1.20)
        assert rec == pytest.approx(summary.agl_max_m * 0.5, rel=1e-9)


class TestCoverageGateUsesTheScannedPopulation:
    """The gate was evaluated on the working set, which filter_photos had
    already reduced to nadir — so the denominator excluded exactly the frames
    that fail. Measured on Training1: 14 of 101 read as coverage 1.000."""

    def test_population_denominator_refuses_a_thin_claim(self):
        measured = nadir_frames([1.0] * 14)
        # 87 further frames on the card that could not be measured.
        population = measured + [
            gsd._unmeasurable(f"R{i}.DNG", gsd.REASON_NO_PIXEL_DIMENSIONS)
            for i in range(87)
        ]
        without = gsd.summarize(measured)
        with_pop = gsd.summarize(measured, population=population)

        assert without.sufficient is True          # the old, inert behaviour
        assert without.coverage == pytest.approx(1.0)
        assert with_pop.sufficient is False        # the gate now bites
        assert with_pop.coverage == pytest.approx(14 / 101, rel=1e-6)
        assert any("coverage" in w for w in with_pop.warnings)

    def test_population_does_not_change_the_statistics(self):
        measured = nadir_frames([1.0, 1.1, 1.2, 1.3, 1.4] * 8)
        population = measured + [
            gsd._unmeasurable(f"R{i}.DNG", gsd.REASON_NO_PIXEL_DIMENSIONS)
            for i in range(5)
        ]
        a = gsd.summarize(measured)
        b = gsd.summarize(measured, population=population)
        assert b.median_cm == pytest.approx(a.median_cm)
        assert b.p95_cm == pytest.approx(a.p95_cm)
        assert b.measured == a.measured

    def test_omitting_population_is_backward_compatible(self):
        frames = nadir_frames([1.0] * 30)
        assert gsd.summarize(frames).coverage == pytest.approx(
            gsd.summarize(frames, population=None).coverage)


class TestReportNeverHidesTheMeasuredLimit:
    """A raster's pixel size is a processing parameter. ODM will emit a 1.00
    cm/px raster from a flight that sampled the ground every 1.25 cm; the
    achieved branch used to return early so the measured value never appeared."""

    def test_resampled_raster_is_labelled_as_such(self):
        summary = gsd.summarize(nadir_frames([1.2494] * 40))
        achieved = {"gsd_cm": 1.00, "source": "odm_orthophoto.tif"}
        label, text = gsd.report_resolution_row(summary=summary, achieved=achieved)
        assert label == "Orthomosaic Resolution"
        assert "1.00 cm/pixel" in text
        assert "1.25" in text                     # the measured limit is present
        assert "not extra detail" in text

    def test_honest_coarser_raster_stays_a_plain_row(self):
        # Raster coarser than the capture supports = no over-claim. The
        # delivered number stays the headline and alone, preserving the
        # ranking rule (see TestSurfaces::test_delivered_raster_outranks...).
        summary = gsd.summarize(nadir_frames([1.00] * 40))
        achieved = {"gsd_cm": 2.00, "source": "odm_orthophoto.tif"}
        _, text = gsd.report_resolution_row(summary=summary, achieved=achieved)
        assert text == "2.00 cm/pixel (measured from the delivered GeoTIFF)"
        assert "not extra detail" not in text

    def test_no_summary_keeps_the_plain_raster_row(self):
        achieved = {"gsd_cm": 1.00, "source": "odm_orthophoto.tif"}
        _, text = gsd.report_resolution_row(summary=None, achieved=achieved)
        assert text == "1.00 cm/pixel (measured from the delivered GeoTIFF)"


class TestRefusalIsNotFollowedByTheNumber:
    """806Mead printed 'GSD: not measured (2 of 31)' and then
    'median 2.26 cm/px' two lines below it."""

    def test_insufficient_summary_tags_its_camera_line(self):
        summary = gsd.summarize(nadir_frames([2.26] * 2))   # below MIN_MEASURED_FRAMES
        assert not summary.sufficient
        lines = gsd.summary_lines(summary)
        camera = [l for l in lines if l.startswith("Camera:")]
        assert camera, "camera line should still be shown as diagnostics"
        assert all("below claim threshold" in l for l in camera)

    def test_sufficient_summary_has_no_tag(self):
        summary = gsd.summarize(nadir_frames([1.0] * 40))
        assert summary.sufficient
        camera = [l for l in gsd.summary_lines(summary) if l.startswith("Camera:")]
        assert camera and all("below claim threshold" not in l for l in camera)
