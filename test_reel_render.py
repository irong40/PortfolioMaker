"""Tests for reel_render — edit-plan math, window scoring, cards, ffmpeg assembly."""

import pytest

from reel_render import (
    INTRO_S,
    MAX_SEG_S,
    MIN_SEG_S,
    OUTRO_S,
    XFADE_S,
    best_window,
    build_assembly_cmd,
    choose_segmentation,
    derive_cut,
    make_card,
    plan_duration,
    plan_reel,
    window_score,
    xfade_offsets,
)


def fake_clip(path, duration, motion=0.1, brightness=0.4):
    samples = [{"t": t * 0.33, "motion": motion, "brightness": brightness}
               for t in range(int(duration * 3))]
    return {"path": path, "duration": duration, "samples": samples}


class TestXfadeOffsets:
    def test_two_segments(self):
        assert xfade_offsets([3.0, 5.0]) == [2.5]

    def test_chain_accumulates(self):
        # 3s, 5s, 4s with 0.5 xfade: offsets at 2.5 and 7.0
        assert xfade_offsets([3.0, 5.0, 4.0]) == [2.5, 7.0]

    def test_single_segment_no_offsets(self):
        assert xfade_offsets([10.0]) == []


class TestChooseSegmentation:
    @pytest.mark.parametrize("target,n_clips", [(45, 11), (60, 11), (90, 17)])
    def test_hits_target_exactly_with_enough_clips(self, target, n_clips):
        n, seg = choose_segmentation(target, n_clips)
        achieved = INTRO_S + OUTRO_S + n * seg - XFADE_S * (n + 1)
        assert achieved == pytest.approx(target, abs=0.01)
        assert MIN_SEG_S <= seg <= MAX_SEG_S

    def test_few_clips_falls_short_not_dragged(self):
        n, seg = choose_segmentation(90, 3)
        assert n == 3
        assert seg == MAX_SEG_S  # capped, reel comes out short of 90

    def test_no_clips_raises(self):
        with pytest.raises(ValueError, match="no usable clips"):
            choose_segmentation(60, 0)


class TestPlanReel:
    def test_plan_structure(self):
        clips = [fake_clip(f"c{i:02d}.mp4", 30.0) for i in range(11)]
        plan = plan_reel(clips, 60.0)
        assert plan[0] == {"type": "card", "card": "intro", "dur": INTRO_S}
        assert plan[-1] == {"type": "card", "card": "outro", "dur": OUTRO_S}
        assert all(p["type"] == "clip" for p in plan[1:-1])

    def test_timeline_hits_target(self):
        clips = [fake_clip(f"c{i:02d}.mp4", 30.0) for i in range(11)]
        plan = plan_reel(clips, 60.0)
        assert plan_duration(plan) == pytest.approx(60.0, abs=0.01)

    def test_chronological_order_kept(self):
        clips = [fake_clip(f"c{i:02d}.mp4", 30.0, motion=0.1 * (i % 3 + 1))
                 for i in range(11)]
        plan = plan_reel(clips, 60.0)
        paths = [p["path"] for p in plan if p["type"] == "clip"]
        assert paths == sorted(paths)

    def test_high_motion_clips_win(self):
        # target 37s -> 6 body segments (verified), exactly the lively clip count
        dull = [fake_clip(f"a{i}.mp4", 20.0, motion=0.01) for i in range(6)]
        lively = [fake_clip(f"b{i}.mp4", 20.0, motion=0.5) for i in range(6)]
        plan = plan_reel(dull + lively, 37.0)
        picked = {p["path"] for p in plan if p["type"] == "clip"}
        assert len(picked) == 6
        assert all(p.startswith("b") for p in picked)

    def test_short_clips_used_whole(self):
        clips = [fake_clip("short.mp4", 4.0)] + \
                [fake_clip(f"c{i}.mp4", 30.0, motion=0.01) for i in range(10)]
        plan = plan_reel(clips, 60.0)
        short = [p for p in plan if p.get("path") == "short.mp4"]
        assert short and short[0]["dur"] == 4.0 and short[0]["start"] == 0.0

    def test_too_short_clips_excluded(self):
        clips = [fake_clip("tiny.mp4", 1.0, motion=0.9),
                 fake_clip("ok.mp4", 30.0)]
        plan = plan_reel(clips, 45.0)
        assert all(p.get("path") != "tiny.mp4" for p in plan)

    def test_all_too_short_raises(self):
        with pytest.raises(ValueError, match="no clips"):
            plan_reel([fake_clip("tiny.mp4", 1.0)], 45.0)


class TestWindowScoring:
    def test_dark_static_scores_near_zero(self):
        samples = [{"t": t, "motion": 0.001, "brightness": 0.01} for t in range(10)]
        assert window_score(samples) < 0.001

    def test_dark_with_bursts_beats_dark_static(self):
        static = [{"t": t, "motion": 0.001, "brightness": 0.02} for t in range(10)]
        bursts = [{"t": t, "motion": 0.3, "brightness": 0.15} for t in range(10)]
        assert window_score(bursts) > window_score(static) * 10

    def test_best_window_finds_burst(self):
        samples = ([{"t": t * 1.0, "motion": 0.001, "brightness": 0.3} for t in range(20)] +
                   [{"t": 20.0 + t, "motion": 0.5, "brightness": 0.4} for t in range(10)])
        start, _ = best_window(samples, 30.0, 6.0)
        assert start >= 18.0

    def test_clip_shorter_than_window(self):
        assert best_window([], 4.0, 6.0) == (0.0, 0.0)


class TestCards:
    JOB = {"site": "806 Meads Ct", "address": "806 Meads Ct, Chesapeake, VA",
           "agent": {"name": "Jane Realtor", "phone": "757-555-0100"}}

    def test_intro_card_renders(self, tmp_path):
        from PIL import Image
        out = make_card("intro", self.JOB, str(tmp_path / "intro.png"), size=(960, 540))
        img = Image.open(out)
        assert img.size == (960, 540)

    def test_outro_card_without_agent(self, tmp_path):
        job = dict(self.JOB, agent=None)
        out = make_card("outro", job, str(tmp_path / "outro.png"), size=(960, 540))
        from PIL import Image
        assert Image.open(out).size == (960, 540)


class TestAssemblyCmd:
    PLAN = [
        {"type": "card", "card": "intro", "dur": 3.0},
        {"type": "clip", "path": "a.mp4", "start": 2.0, "dur": 5.85},
        {"type": "clip", "path": "b.mp4", "start": 0.0, "dur": 5.85},
        {"type": "card", "card": "outro", "dur": 4.0},
    ]
    CARDS = {"intro": "intro.png", "outro": "outro.png"}

    def test_native_audio_mode(self):
        cmd = build_assembly_cmd(self.PLAN, {"a.mp4": True, "b.mp4": True},
                                 self.CARDS, "out.mp4", music_track=None)
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.count("xfade=") == 3          # 4 segments -> 3 transitions
        assert graph.count("acrossfade=") == 3
        assert cmd.count("anullsrc=r=48000:cl=stereo") == 2  # two cards need silence
        assert "hevc_nvenc" in cmd

    def test_music_mode_skips_native_audio(self):
        cmd = build_assembly_cmd(self.PLAN, {"a.mp4": True, "b.mp4": True},
                                 self.CARDS, "out.mp4", music_track="pool/track.wav")
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert "acrossfade" not in graph
        assert "afade=t=out" in graph
        assert "pool/track.wav" in cmd

    def test_soundless_clip_gets_silence(self):
        cmd = build_assembly_cmd(self.PLAN, {"a.mp4": True, "b.mp4": False},
                                 self.CARDS, "out.mp4", music_track=None)
        assert cmd.count("anullsrc=r=48000:cl=stereo") == 3

    def test_output_duration_capped(self):
        cmd = build_assembly_cmd(self.PLAN, {"a.mp4": True, "b.mp4": True},
                                 self.CARDS, "out.mp4", music_track=None)
        t_idx = len(cmd) - 1 - cmd[::-1].index("-t")
        assert float(cmd[t_idx + 1]) == pytest.approx(plan_duration(self.PLAN), abs=0.01)


class TestDeriveCut:
    def test_web_1080p(self):
        cmd = derive_cut("master.mp4", "web.mp4", "web_1080p")
        assert "scale=1920:1080" in cmd

    def test_vertical_916(self):
        cmd = derive_cut("master.mp4", "vert.mp4", "vertical_916")
        vf = cmd[cmd.index("-vf") + 1]
        assert "crop=ih*9/16:ih" in vf and "1080:1920" in vf

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            derive_cut("m.mp4", "o.mp4", "imax")
