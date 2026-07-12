"""Tests for reel_job — reel job contract, package presets, and queue state machine."""

import json

import pytest

from reel_job import (
    PACKAGE_PRESETS,
    SCHEMA_ID,
    build_reel_job,
    claim_job,
    complete_job,
    enqueue_reel_job,
    fail_job,
    load_reel_job,
    log_music_usage,
    next_job,
    pick_music_track,
    release_job,
    slugify,
    validate_reel_job,
)

FIXED_TS = "20260705_120000"


def make_job(**overrides):
    """Minimal valid job for tests."""
    kwargs = dict(
        package="listing_pro",
        site="806 Meads Ct",
        address="806 Meads Ct, Chesapeake, VA 23322",
        source_dir="F:/DCIM/DJI_001",
        clips=[{"path": "F:/DCIM/DJI_001/DJI_0001.MP4", "name": "DJI_0001.MP4",
                "has_srt": True, "srt_path": "F:/DCIM/DJI_001/DJI_0001.SRT"}],
        created=FIXED_TS,
    )
    kwargs.update(overrides)
    return build_reel_job(**kwargs)


class TestPackagePresets:
    def test_all_six_packages_registered(self):
        expected = {
            "listing_lite", "listing_pro", "luxury",
            "commercial_marketing", "construction", "inspection",
        }
        assert set(PACKAGE_PRESETS.keys()) == expected

    @pytest.mark.parametrize("package,duration", [
        ("listing_lite", 45),
        ("listing_pro", 60),
        ("luxury", 90),
        ("commercial_marketing", 90),
    ])
    def test_locked_durations(self, package, duration):
        """Reel lengths locked 2026-07-05: 45/60/90 per package tier."""
        assert PACKAGE_PRESETS[package]["duration_s"] == duration

    @pytest.mark.parametrize("package,mood", [
        ("listing_lite", "calm"),
        ("listing_pro", "upbeat"),
        ("luxury", "upbeat"),
        ("commercial_marketing", "upbeat"),
        ("construction", "corporate"),
        ("inspection", "corporate"),
    ])
    def test_locked_music_moods(self, package, mood):
        """Mood map locked 2026-07-05: calm=Lite, upbeat=Pro+, corporate=Construction/Inspection."""
        assert PACKAGE_PRESETS[package]["music_mood"] == mood


class TestSlugify:
    def test_basic(self):
        assert slugify("806 Meads Ct") == "806-meads-ct"

    def test_strips_punctuation_and_repeats(self):
        assert slugify("Portsmouth/Norfolk -- Fireworks!") == "portsmouth-norfolk-fireworks"

    def test_empty_falls_back(self):
        assert slugify("") == "unnamed"


class TestBuildReelJob:
    def test_valid_job_passes_validation(self):
        job = make_job()
        assert validate_reel_job(job) == []

    def test_schema_and_job_id(self):
        job = make_job()
        assert job["schema"] == SCHEMA_ID
        assert job["job_id"] == f"{FIXED_TS}_806-meads-ct"

    def test_duration_derived_from_package(self):
        assert make_job(package="listing_lite")["render"]["duration_s"] == 45
        assert make_job(package="luxury")["render"]["duration_s"] == 90

    def test_mood_derived_from_package(self):
        assert make_job(package="listing_lite")["music"]["mood"] == "calm"
        assert make_job(package="inspection")["music"]["mood"] == "corporate"

    def test_address_overlay_always_on(self):
        """Locked decision: address overlay on ALL packages, including Lite."""
        for package in PACKAGE_PRESETS:
            assert make_job(package=package)["render"]["overlay_address"] is True

    def test_agent_card_requires_agent(self):
        """Agent card on all packages — but only when agent info exists."""
        assert make_job()["render"]["agent_card"] is False
        with_agent = make_job(agent={"name": "Jane Realtor", "phone": "757-555-0100"})
        assert with_agent["render"]["agent_card"] is True

    def test_map_card_default_on(self):
        """Map card requested on all packages; renderer skips without data."""
        for package in PACKAGE_PRESETS:
            assert make_job(package=package)["render"]["map_card"] is True

    def test_unknown_package_raises(self):
        with pytest.raises(ValueError, match="unknown package"):
            make_job(package="platinum_deluxe")


class TestValidateReelJob:
    def test_missing_site(self):
        job = make_job()
        job["site"] = ""
        assert any("site" in p for p in validate_reel_job(job))

    def test_wrong_schema(self):
        job = make_job()
        job["schema"] = "sai.reel-job/999"
        assert any("schema" in p for p in validate_reel_job(job))

    def test_no_inputs(self):
        job = make_job()
        job["inputs"] = {"clips": [], "panos": [], "photos": []}
        assert any("input" in p for p in validate_reel_job(job))

    def test_photos_only_is_valid(self):
        """Ken Burns photos-only reel is a supported Listing Lite path."""
        job = make_job(clips=[], photos=["E:/Portfolio/806/oblique/DJI_0042.JPG"])
        assert validate_reel_job(job) == []

    def test_clip_missing_path(self):
        job = make_job(clips=[{"name": "DJI_0001.MP4"}])
        assert any("path" in p for p in validate_reel_job(job))


class TestQueue:
    def test_enqueue_writes_json(self, tmp_path):
        job = make_job()
        path = enqueue_reel_job(job, queue_dir=tmp_path)
        assert path.name == f"{job['job_id']}.json"
        assert json.loads(path.read_text(encoding="utf-8"))["site"] == "806 Meads Ct"

    def test_enqueue_rejects_invalid(self, tmp_path):
        job = make_job()
        job["site"] = ""
        with pytest.raises(ValueError, match="site"):
            enqueue_reel_job(job, queue_dir=tmp_path)

    def test_load_round_trip(self, tmp_path):
        path = enqueue_reel_job(make_job(), queue_dir=tmp_path)
        job = load_reel_job(path)
        assert job["job_id"] == f"{FIXED_TS}_806-meads-ct"

    def test_next_job_picks_oldest(self, tmp_path):
        enqueue_reel_job(make_job(created="20260705_120100", site="Later Site"),
                         queue_dir=tmp_path)
        enqueue_reel_job(make_job(), queue_dir=tmp_path)
        assert next_job(tmp_path).name.startswith(FIXED_TS)

    def test_next_job_empty_queue(self, tmp_path):
        assert next_job(tmp_path) is None
        assert next_job(tmp_path / "does-not-exist") is None

    def test_claim_release_cycle(self, tmp_path):
        path = enqueue_reel_job(make_job(), queue_dir=tmp_path)
        claimed = claim_job(path)
        assert claimed.suffix == ".rendering"
        assert next_job(tmp_path) is None  # claimed job no longer visible
        released = release_job(claimed)
        assert released.suffix == ".json"
        assert next_job(tmp_path) == released

    def test_complete_writes_result_and_renames(self, tmp_path):
        path = enqueue_reel_job(make_job(), queue_dir=tmp_path)
        claimed = claim_job(path)
        done = complete_job(claimed, outputs={"master_4k": "E:/out/reel_4k.mp4"},
                            music_track="upbeat/take3.wav")
        assert done.suffix == ".done"
        result = json.loads(
            (tmp_path / f"{FIXED_TS}_806-meads-ct.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "done"
        assert result["outputs"]["master_4k"] == "E:/out/reel_4k.mp4"
        assert result["error"] is None

    def test_fail_writes_result_and_renames(self, tmp_path):
        path = enqueue_reel_job(make_job(), queue_dir=tmp_path)
        claimed = claim_job(path)
        failed = fail_job(claimed, error="ffmpeg exploded")
        assert failed.suffix == ".failed"
        result = json.loads(
            (tmp_path / f"{FIXED_TS}_806-meads-ct.result.json").read_text(encoding="utf-8"))
        assert result["status"] == "failed"
        assert result["error"] == "ffmpeg exploded"


class TestPickMusicTrack:
    def _pool(self, tmp_path):
        for mood in ("calm", "upbeat", "corporate"):
            d = tmp_path / mood
            d.mkdir()
            for i in range(3):
                (d / f"{mood}_take{i}.wav").write_bytes(b"RIFF")
        return tmp_path

    def test_deterministic_for_same_job(self, tmp_path):
        pool = self._pool(tmp_path)
        job = make_job()
        assert pick_music_track(job, pool) == pick_music_track(job, pool)

    def test_picks_from_mood_folder(self, tmp_path):
        pool = self._pool(tmp_path)
        track = pick_music_track(make_job(package="listing_lite"), pool)
        assert track.parent.name == "calm"

    def test_empty_pool_returns_none(self, tmp_path):
        assert pick_music_track(make_job(), tmp_path) is None

    def test_explicit_track_override_wins(self, tmp_path):
        pool = self._pool(tmp_path)
        job = make_job()
        job["music"]["track"] = str(pool / "corporate" / "corporate_take0.wav")
        assert pick_music_track(job, pool).name == "corporate_take0.wav"


class TestLogMusicUsage:
    def test_creates_ledger_with_header_and_row(self, tmp_path):
        job = make_job()
        path = log_music_usage(job, tmp_path / "upbeat" / "upbeat-01-110.wav",
                               tmp_path)
        lines = path.read_text().strip().splitlines()
        assert lines[0].startswith("rendered_utc,job_id,site,package,mood,track")
        assert job["job_id"] in lines[1]
        assert "upbeat-01-110.wav" in lines[1]

    def test_appends_without_duplicate_header(self, tmp_path):
        log_music_usage(make_job(), tmp_path / "a.wav", tmp_path)
        path = log_music_usage(make_job(), tmp_path / "b.wav", tmp_path)
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3  # header + 2 rows
        assert sum(1 for ln in lines if ln.startswith("rendered_utc")) == 1

    def test_none_track_logs_native_audio(self, tmp_path):
        path = log_music_usage(make_job(), None, tmp_path)
        assert "native-audio" in path.read_text().strip().splitlines()[1]
