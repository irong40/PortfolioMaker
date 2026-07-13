"""Tests for reel_render — edit-plan math, window scoring, cards, ffmpeg assembly."""

import pytest

from reel_render import (
    DEFAULT_LUT,
    INTRO_S,
    MAP_S,
    MAX_SEG_S,
    MIN_SEG_S,
    OUTRO_S,
    OVERLAY_FONT,
    XFADE_S,
    _address_overlay_filter,
    _kenburns_filter,
    _lut_filter,
    best_window,
    build_assembly_cmd,
    choose_segmentation,
    clip_color_mode,
    derive_cut,
    make_card,
    make_map_card,
    plan_duration,
    plan_photo_reel,
    plan_reel,
    resolve_lut,
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


def write_srt(path, points):
    """Minimal DJI-style SRT with one telemetry block per point."""
    blocks = []
    for i, (lat, lon) in enumerate(points):
        t = i / 30
        m, s = divmod(t, 60)
        start = f"00:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"
        blocks.append(
            f"{i + 1}\n{start} --> {start}\n"
            f'<font size="28">[latitude: {lat:.6f}] [longitude: {lon:.6f}] '
            f"[rel_alt: 30.500] [focal_len: 24.00]</font>\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return str(path)


class TestMapCard:
    def make_job(self, tmp_path, with_srt=True, kml_path=None):
        clips = []
        if with_srt:
            srt = write_srt(tmp_path / "DJI_0001.SRT",
                            [(36.75 + i * 1e-4, -76.25 + i * 5e-5)
                             for i in range(90)])
            clips.append({"path": "DJI_0001.MP4", "name": "DJI_0001.MP4",
                          "has_srt": True, "srt_path": srt})
        return {"site": "806 Meads Ct", "address": "806 Meads Ct, Chesapeake",
                "kml_path": kml_path, "inputs": {"clips": clips}}

    def test_renders_from_srt(self, tmp_path):
        from PIL import Image
        job = self.make_job(tmp_path)
        out = make_map_card(job, str(tmp_path / "map.png"), size=(960, 540))
        assert out is not None
        assert Image.open(out).size == (960, 540)

    def test_none_without_data(self, tmp_path):
        job = self.make_job(tmp_path, with_srt=False)
        assert make_map_card(job, str(tmp_path / "map.png")) is None

    def test_none_with_missing_srt_file(self, tmp_path):
        job = self.make_job(tmp_path, with_srt=False)
        job["inputs"]["clips"] = [{"path": "x.MP4", "name": "x.MP4",
                                   "has_srt": True,
                                   "srt_path": str(tmp_path / "gone.SRT")}]
        assert make_map_card(job, str(tmp_path / "map.png")) is None


class TestPlanWithMapCard:
    def test_map_card_before_outro(self):
        clips = [fake_clip(f"c{i}.mp4", 12.0) for i in range(11)]
        plan = plan_reel(clips, 60, map_card=True)
        cards = [p["card"] for p in plan if p["type"] == "card"]
        assert cards == ["intro", "map", "outro"]
        assert plan[-2]["card"] == "map" and plan[-2]["dur"] == MAP_S

    def test_timeline_still_hits_target(self):
        clips = [fake_clip(f"c{i}.mp4", 12.0) for i in range(11)]
        plan = plan_reel(clips, 60, map_card=True)
        assert plan_duration(plan) == pytest.approx(60, abs=0.01)

    def test_default_plan_unchanged(self):
        clips = [fake_clip(f"c{i}.mp4", 12.0) for i in range(11)]
        plan = plan_reel(clips, 60)
        cards = [p["card"] for p in plan if p["type"] == "card"]
        assert cards == ["intro", "outro"]


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

    def test_master_pins_yuv420p(self):
        """Regression: unpinned pix_fmt negotiated to 4:4:4, unplayable in most players."""
        cmd = build_assembly_cmd(self.PLAN, {"a.mp4": True, "b.mp4": True},
                                 self.CARDS, "out.mp4", music_track=None)
        assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
        assert cmd[cmd.index("-profile:v") + 1] == "main"

    def test_output_duration_capped(self):
        cmd = build_assembly_cmd(self.PLAN, {"a.mp4": True, "b.mp4": True},
                                 self.CARDS, "out.mp4", music_track=None)
        t_idx = len(cmd) - 1 - cmd[::-1].index("-t")
        assert float(cmd[t_idx + 1]) == pytest.approx(plan_duration(self.PLAN), abs=0.01)


class TestDeriveCut:
    def test_web_1080p(self):
        cmd = derive_cut("master.mp4", "web.mp4", "web_1080p")
        vf = cmd[cmd.index("-vf") + 1]
        assert "scale=1920:1080" in vf

    def test_vertical_916(self):
        cmd = derive_cut("master.mp4", "vert.mp4", "vertical_916")
        vf = cmd[cmd.index("-vf") + 1]
        assert "crop='trunc(ih*9/16/2)*2':ih" in vf and "1080:1920" in vf

    @pytest.mark.parametrize("kind", ["web_1080p", "vertical_916"])
    def test_cuts_pin_yuv420p(self, kind):
        """Regression: derived cuts inherited 4:4:4 from the master."""
        cmd = derive_cut("master.mp4", "out.mp4", kind)
        assert cmd[cmd.index("-vf") + 1].endswith("format=yuv420p")
        assert cmd[cmd.index("-profile:v") + 1] == "high"

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            derive_cut("m.mp4", "o.mp4", "imax")


SRT_BLOCK = """1
00:00:00,000 --> 00:00:00,033
<font size="28">FrameCnt: 1, DiffTime: 33ms
2026-06-19 16:14:44.416
[iso: 110] [shutter: 1/4000.0] [fnum: 1.7] [ev: 0] [color_md: {mode}] [focal_len: 72.00] [latitude: 36.795162] [longitude: -76.405117] [rel_alt: 69.700 abs_alt: 119.745] [ct: 5700] </font>
"""


class TestColorMode:
    def test_dlog_m_detected(self, tmp_path):
        srt = tmp_path / "clip.SRT"
        srt.write_text(SRT_BLOCK.format(mode="dlog_m"), encoding="utf-8")
        assert clip_color_mode(str(srt)) == "dlog_m"

    def test_normal_profile(self, tmp_path):
        srt = tmp_path / "clip.SRT"
        srt.write_text(SRT_BLOCK.format(mode="default"), encoding="utf-8")
        assert clip_color_mode(str(srt)) == "default"

    def test_missing_file_none(self, tmp_path):
        assert clip_color_mode(str(tmp_path / "nope.SRT")) is None

    def test_no_srt_path_none(self):
        assert clip_color_mode(None) is None

    def test_no_color_tag_none(self, tmp_path):
        srt = tmp_path / "clip.SRT"
        srt.write_text("1\n00:00:00,000 --> 00:00:00,033\nplain subtitle\n",
                       encoding="utf-8")
        assert clip_color_mode(str(srt)) is None


class TestResolveLut:
    def test_explicit_lut_wins(self, tmp_path):
        cube = tmp_path / "custom.cube"
        cube.write_text("LUT_3D_SIZE 2\n")
        job = {"render": {"lut": str(cube)}}
        assert resolve_lut(job) == str(cube)

    def test_explicit_missing_falls_to_none(self, tmp_path):
        """A named-but-absent LUT must not silently grade with the default."""
        job = {"render": {"lut": str(tmp_path / "gone.cube")}}
        assert resolve_lut(job) is None

    def test_null_uses_repo_default(self):
        job = {"render": {"lut": None}}
        expected = str(DEFAULT_LUT) if DEFAULT_LUT.exists() else None
        assert resolve_lut(job) == expected

    def test_repo_default_shipped(self):
        """The DJI D-Log M cube ships with the repo — D-Log footage is the norm."""
        assert DEFAULT_LUT.exists()


class TestLutFilter:
    def test_windows_path_escaped(self):
        atom = _lut_filter("D:\\Projects\\PortfolioMaker\\assets\\luts\\x.cube")
        assert atom == "lut3d='D\\:/Projects/PortfolioMaker/assets/luts/x.cube'"

    def test_assembly_grades_only_flagged_clips(self):
        plan = TestAssemblyCmd.PLAN
        cmd = build_assembly_cmd(plan, {"a.mp4": True, "b.mp4": True},
                                 TestAssemblyCmd.CARDS, "out.mp4", music_track=None,
                                 clip_luts={"a.mp4": "luts/dji.cube"})
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.count("lut3d=") == 1
        # graded clip: LUT applied before scaling; input 1 is clip a.mp4
        assert "[1:v]lut3d='luts/dji.cube',scale=" in graph
        # cards (inputs 0, 3) and the normal-profile clip (input 2) untouched
        assert "[0:v]scale=" in graph and "[2:v]scale=" in graph and "[3:v]scale=" in graph

    def test_assembly_no_luts_unchanged(self):
        plan = TestAssemblyCmd.PLAN
        with_none = build_assembly_cmd(plan, {"a.mp4": True, "b.mp4": True},
                                       TestAssemblyCmd.CARDS, "out.mp4", music_track=None)
        with_empty = build_assembly_cmd(plan, {"a.mp4": True, "b.mp4": True},
                                        TestAssemblyCmd.CARDS, "out.mp4", music_track=None,
                                        clip_luts={})
        assert with_none == with_empty
        assert "lut3d" not in " ".join(with_none)


class TestAddressOverlay:
    @pytest.mark.skipif(not OVERLAY_FONT.exists(), reason="overlay font not installed")
    def test_atom_centered_boxed(self, tmp_path):
        atom = _address_overlay_filter("806 Meads Ct, Chesapeake, VA 23322",
                                       3840, 2160, tmp_path)
        assert atom.startswith("drawtext=fontfile=")
        assert "x=(w-text_w)/2" in atom          # centered -> survives 9:16 crop
        assert "box=1" in atom
        assert "textfile=" in atom and "text='" not in atom

    @pytest.mark.skipif(not OVERLAY_FONT.exists(), reason="overlay font not installed")
    def test_text_goes_through_file_verbatim(self, tmp_path):
        """Apostrophes broke inline text= (double parsing) — textfile carries
        the address untouched, no escaping of content at all."""
        _address_overlay_filter("12 O'Neill Rd", 3840, 2160, tmp_path)
        assert (tmp_path / "address_overlay.txt").read_text(
            encoding="utf-8") == "12 O'Neill Rd"

    def test_missing_font_none(self, tmp_path):
        assert _address_overlay_filter("x", 3840, 2160, tmp_path,
                                       font=tmp_path / "nope.ttf") is None

    @pytest.mark.skipif(not OVERLAY_FONT.exists(), reason="overlay font not installed")
    def test_long_address_shrinks_to_fit_vertical_crop(self, tmp_path):
        import re
        short = _address_overlay_filter("806 Meads Ct", 3840, 2160, tmp_path)
        long = _address_overlay_filter(
            "1234 Chesapeake Boulevard Extended, Virginia Beach, VA 23456-1234",
            3840, 2160, tmp_path)
        size = lambda atom: int(re.search(r"fontsize=(\d+)", atom).group(1))
        assert size(long) < size(short)
        # long address at its fontsize must fit the 9:16 crop width
        assert size(long) * 0.58 * 66 <= 2160 * 9 / 16

    def test_assembly_overlays_body_not_cards(self):
        cmd = build_assembly_cmd(TestAssemblyCmd.PLAN, {"a.mp4": True, "b.mp4": True},
                                 TestAssemblyCmd.CARDS, "out.mp4", music_track=None,
                                 body_overlay="drawtext=text='addr'")
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.count("drawtext") == 2       # the two clips
        assert "[0:v]scale" in graph              # intro card untouched
        assert "[3:v]scale" in graph and "[3:v]drawtext" not in graph

    def test_assembly_default_no_overlay(self):
        cmd = build_assembly_cmd(TestAssemblyCmd.PLAN, {"a.mp4": True, "b.mp4": True},
                                 TestAssemblyCmd.CARDS, "out.mp4", music_track=None)
        assert "drawtext" not in cmd[cmd.index("-filter_complex") + 1]

    def test_overlay_composes_with_lut(self):
        """Graded D-Log clip gets LUT first, then the overlay, then encode chain."""
        cmd = build_assembly_cmd(TestAssemblyCmd.PLAN, {"a.mp4": True, "b.mp4": True},
                                 TestAssemblyCmd.CARDS, "out.mp4", music_track=None,
                                 clip_luts={"a.mp4": "d.cube"},
                                 body_overlay="drawtext=text='addr'")
        graph = cmd[cmd.index("-filter_complex") + 1]
        chain = [f for f in graph.split(";") if f.startswith("[1:v]")][0]
        assert chain.index("lut3d") < chain.index("scale") < chain.index("drawtext")


class TestPhotoPlan:
    PHOTOS = [f"DJI_2026070{i}.JPG" for i in range(1, 9)]

    def test_structure(self):
        plan = plan_photo_reel(self.PHOTOS, 60)
        assert plan[0] == {"type": "card", "card": "intro", "dur": INTRO_S}
        assert plan[-1] == {"type": "card", "card": "outro", "dur": OUTRO_S}
        assert all(p["type"] == "photo" for p in plan[1:-1])

    def test_timeline_hits_target(self):
        plan = plan_photo_reel(self.PHOTOS, 60)
        assert plan_duration(plan) == pytest.approx(60, abs=1.0)

    def test_even_spread_keeps_order_and_endpoints(self):
        plan = plan_photo_reel(self.PHOTOS, 45)
        picked = [p["path"] for p in plan if p["type"] == "photo"]
        assert picked == sorted(picked)
        if len(picked) < len(self.PHOTOS):
            assert picked[0] == self.PHOTOS[0]
            assert picked[-1] == self.PHOTOS[-1]

    def test_map_card_before_outro(self):
        plan = plan_photo_reel(self.PHOTOS, 60, map_card=True)
        assert plan[-2] == {"type": "card", "card": "map", "dur": MAP_S}

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            plan_photo_reel([], 60)


class TestPhotoAssembly:
    PLAN = [
        {"type": "card", "card": "intro", "dur": 3.0},
        {"type": "photo", "path": "p1.jpg", "dur": 5.5},
        {"type": "photo", "path": "p2.jpg", "dur": 5.5},
        {"type": "card", "card": "outro", "dur": 4.0},
    ]
    CARDS = {"intro": "intro.png", "outro": "outro.png"}

    def test_photo_inputs_not_seeked(self):
        cmd = build_assembly_cmd(self.PLAN, {}, self.CARDS, "out.mp4",
                                 music_track="pool/track.wav")
        p1 = cmd.index("p1.jpg")
        assert cmd[p1 - 1] == "-i" and cmd[p1 - 2] != "-ss"

    def test_photos_get_kenburns(self):
        cmd = build_assembly_cmd(self.PLAN, {}, self.CARDS, "out.mp4",
                                 music_track="pool/track.wav")
        graph = cmd[cmd.index("-filter_complex") + 1]
        assert graph.count("zoompan") == 2
        assert graph.count("xfade=") == 3

    def test_photos_silent_in_native_mode(self):
        cmd = build_assembly_cmd(self.PLAN, {}, self.CARDS, "out.mp4",
                                 music_track=None)
        # 2 cards + 2 soundless photos -> 4 silence sources
        assert cmd.count("anullsrc=r=48000:cl=stereo") == 4

    def test_kenburns_moves_alternate(self):
        moves = {_kenburns_filter(i, 5.5, 3840, 2160) for i in range(4)}
        assert len(moves) == 4                     # all four variants distinct
        assert _kenburns_filter(0, 5.5, 3840, 2160) == \
            _kenburns_filter(4, 5.5, 3840, 2160)   # deterministic cycle

    def test_kenburns_outputs_master_geometry(self):
        atom = _kenburns_filter(0, 5.5, 3840, 2160)
        assert "s=3840x2160" in atom and "fps=30" in atom


class TestAgentCardFlag:
    AGENT = {"name": "Jane Realtor", "phone": "757-555-0100"}

    def test_flag_off_forces_sai_outro(self, tmp_path):
        with_flag_off = make_card(
            "outro", {"agent": self.AGENT, "render": {"agent_card": False}},
            str(tmp_path / "off.png"))
        no_agent = make_card("outro", {"agent": None}, str(tmp_path / "none.png"))
        assert (tmp_path / "off.png").read_bytes() == (tmp_path / "none.png").read_bytes()

    def test_default_keeps_agent(self, tmp_path):
        with_agent = make_card("outro", {"agent": self.AGENT},
                               str(tmp_path / "agent.png"))
        no_agent = make_card("outro", {"agent": None}, str(tmp_path / "none.png"))
        assert (tmp_path / "agent.png").read_bytes() != (tmp_path / "none.png").read_bytes()
