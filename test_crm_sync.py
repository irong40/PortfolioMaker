"""Tests for crm_sync — CRM mission fetch, field allowlist, stage helpers."""

import pytest

import crm_sync
from crm_sync import CrmMission, _parse_mission


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")


@pytest.fixture
def unconfigured(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    # Keep load_dotenv from re-injecting real creds from a local .env
    monkeypatch.setattr(crm_sync, "_load_env", lambda: None)


SAMPLE_ROW = {
    "id": "d940f61d-e8fa-40a6-a4b9-a59cb4f0341d",
    "job_number": "SAI-SPEC-012",
    "property_address": "2237 Shillelagh Rd, Chesapeake, VA 23323",
    "site_address": "2237 Shillelagh Rd, Chesapeake, VA 23323",
    "property_city": "Chesapeake",
    "property_state": "VA",
    "property_type": "land",
    "status": "scheduled",
    "scheduled_date": "2026-07-15",
    "scheduled_time": "10:30:00",
    "pilot_notes": None,
    "admin_notes": "portfolio test",
    "clients": {"name": "Jane Doe", "company": "Acme Land"},
    "processing_templates": {"preset_name": "mapping", "path_code": "C",
                             "display_name": "Mapping/WebODM"},
}


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload or []
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


# ── configuration ──────────────────────────────────────────────────────────

def test_is_configured_false_without_env(unconfigured):
    assert not crm_sync.is_configured()


def test_is_configured_true_with_env(configured):
    assert crm_sync.is_configured()


# ── fetch ───────────────────────────────────────────────────────────────────

def test_fetch_parses_missions(configured, monkeypatch):
    monkeypatch.setattr(crm_sync.requests, "get",
                        lambda *a, **k: FakeResponse([SAMPLE_ROW]))
    missions = crm_sync.fetch_open_missions()
    assert len(missions) == 1
    m = missions[0]
    assert m.job_number == "SAI-SPEC-012"
    assert m.client_name == "Jane Doe"
    assert m.preset_name == "mapping"
    assert m.status == "scheduled"


def test_fetch_returns_empty_when_unconfigured(unconfigured):
    assert crm_sync.fetch_open_missions() == []


def test_fetch_returns_empty_on_network_error(configured, monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(crm_sync.requests, "get", boom)
    assert crm_sync.fetch_open_missions() == []


# ── mission helpers ─────────────────────────────────────────────────────────

def test_label_contains_key_facts():
    m = _parse_mission(SAMPLE_ROW)
    assert "SAI-SPEC-012" in m.label
    assert "2237 Shillelagh Rd" in m.label
    assert "scheduled" in m.label


def test_suggested_job_type_mapping():
    m = _parse_mission(SAMPLE_ROW)
    assert m.suggested_job_type() == "property_survey"


def test_suggested_job_type_none_for_video():
    row = dict(SAMPLE_ROW)
    row["processing_templates"] = {"preset_name": "video", "path_code": "V",
                                   "display_name": "Video"}
    assert _parse_mission(row).suggested_job_type() is None


def test_suggested_site_name_is_street():
    m = _parse_mission(SAMPLE_ROW)
    assert m.suggested_site_name() == "2237 Shillelagh Rd"


def test_parse_handles_missing_joins():
    row = {"id": "x", "job_number": "DJ-2026-0001", "clients": None,
           "processing_templates": None}
    m = _parse_mission(row)
    assert m.client_name == ""
    assert m.suggested_job_type() is None
    assert m.suggested_site_name() == "DJ-2026-0001"


# ── update allowlist ────────────────────────────────────────────────────────

def test_update_strips_non_writable_fields(configured, monkeypatch):
    captured = {}

    def fake_patch(url, headers=None, params=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(crm_sync.requests, "patch", fake_patch)
    ok = crm_sync.update_mission("job-1", {
        "status": "complete",
        "job_number": "HACKED",       # not writable — CRM owns naming
        "admin_notes": "nope",        # not writable
    })
    assert ok
    assert captured["json"] == {"status": "complete"}


def test_update_all_dropped_returns_false(configured, monkeypatch):
    monkeypatch.setattr(crm_sync.requests, "patch",
                        lambda *a, **k: FakeResponse())
    assert not crm_sync.update_mission("job-1", {"job_number": "X"})


def test_update_never_raises_on_error(configured, monkeypatch):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(crm_sync.requests, "patch", boom)
    assert crm_sync.update_mission("job-1", {"status": "failed"}) is False


# ── stage helpers ───────────────────────────────────────────────────────────

def _capture_patch(monkeypatch):
    calls = []

    def fake_patch(url, headers=None, params=None, json=None, timeout=None):
        calls.append({"params": params, "json": json})
        return FakeResponse()

    monkeypatch.setattr(crm_sync.requests, "patch", fake_patch)
    return calls


def test_mark_processing(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    assert crm_sync.mark_processing("job-1", photo_count=42)
    body = calls[0]["json"]
    assert body["status"] == "processing"
    assert body["photogrammetry_status"] == "processing"
    assert body["photo_count"] == 42
    assert "processing_started_at" in body


def test_mark_complete_maps_outputs(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    result = {
        "output_dir": r"E:\Portfolio\Site\job",
        "task_uuid": "odm-task-9",
        "downloaded": {
            "odm_orthophoto.tif": r"E:\Portfolio\Site\job\odm_orthophoto.tif",
            "odm_georeferenced_model.laz": r"E:\Portfolio\Site\job\model.laz",
            "odm_textured_model.glb": r"E:\Portfolio\Site\job\model.glb",
        },
    }
    assert crm_sync.mark_complete("job-1", result)
    body = calls[0]["json"]
    assert body["status"] == "complete"
    assert body["delivery_status"] == "ready"
    assert body["nodeodm_task_id"] == "odm-task-9"
    assert body["output_path"] == r"E:\Portfolio\Site\job"
    assert body["orthophoto_path"].endswith("odm_orthophoto.tif")
    assert body["pointcloud_path"].endswith("model.laz")
    assert body["model_file_path"].endswith("model.glb")


def test_mark_failed_truncates_error(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    assert crm_sync.mark_failed("job-1", "x" * 5000)
    body = calls[0]["json"]
    assert body["status"] == "failed"
    assert len(body["processing_error"]) == 1000


def test_record_delivery_sets_drive_url_only(configured, monkeypatch):
    calls = _capture_patch(monkeypatch)
    assert crm_sync.record_delivery("job-1", "https://drive.google.com/x")
    body = calls[0]["json"]
    assert body == {"delivery_drive_url": "https://drive.google.com/x"}


# ── report push ─────────────────────────────────────────────────────────────

def _mission():
    return _parse_mission(SAMPLE_ROW)


def _veg_result(tmp_path):
    """A vegetation process_job result with real temp files for images."""
    heatmap = tmp_path / "vari_heatmap.png"
    heatmap.write_bytes(b"png")
    ortho_prev = tmp_path / "ortho_preview.jpg"
    ortho_prev.write_bytes(b"jpg")
    thumb = tmp_path / "thumb1.jpg"
    thumb.write_bytes(b"jpg")
    ortho = tmp_path / "orthophoto.tif"
    ortho.write_bytes(b"tif" * 100)
    return {
        "output_dir": str(tmp_path),
        "report_data": {
            "site_name": "Shillelagh Rd",
            "date": "2026-07-15",
            "job_type": "vegetation",
            "total_photos": 180,
            "platform": "m4e",
            "engine": "nodeodm",
            "downloads": {"orthophoto.tif": str(ortho)},
            "ai_analysis": {
                "executive_summary": "Healthy stand overall.",
                "canopy_health": "Dense canopy, minor stress at NE corner.",
                "observations": ["Deer trail crossing east parcel."],
                "conditions": "Clear, low wind.",
            },
            "images": {
                "ortho_preview": str(ortho_prev),
                "dsm_preview": None,
                "photo_thumbs": [(str(thumb), "Nadir | -90 pitch")],
            },
            "pc_results": {},
            "veg_results": {
                "veg_pct": 72.4,
                "flagged_polygons": 2,
                "outputs": {"vari_heatmap": str(heatmap)},
            },
        },
    }


def test_build_report_payload_vegetation(tmp_path):
    result = _veg_result(tmp_path)
    section_data, active, images = crm_sync.build_report_payload(
        _mission(), result, "Vegetation Analysis Report")

    cover = section_data["cover_page"]
    assert cover["job_number"] == "SAI-SPEC-012"
    assert cover["client_name"] == "Jane Doe"
    assert cover["report_date"] == "2026-07-15"

    assert section_data["executive_summary"]["summary"] == "Healthy stand overall."
    labels = [m["label"] for m in section_data["executive_summary"]["key_metrics"]]
    assert "Vegetation cover" in labels

    heatmap = section_data["detection_heatmap"]
    assert "72.4%" in heatmap["description"]

    findings = section_data["findings"]["findings"]
    titles = [f["title"] for f in findings]
    assert "Canopy Health" in titles
    assert all(f["review_status"] == "pending" for f in findings)

    manifest = section_data["deliverables_manifest"]["deliverables"]
    assert manifest[0]["type"] == "Orthomosaic"

    assert "detection_heatmap" in active
    assert active.index("cover_page") == 0

    sections_used = {s for s, _, _ in images}
    assert sections_used == {"detection_heatmap", "annotated_imagery"}


def test_build_report_payload_volumetrics(tmp_path):
    result = _veg_result(tmp_path)
    rd = result["report_data"]
    rd["job_type"] = "construction_progress"
    rd["veg_results"] = {}
    rd["pc_results"] = {
        "dsm_comparison": {"fill_volume_m3": 120.5, "cut_volume_m3": 33.3},
        "previous_date": "2026-06-01",
    }
    section_data, active, _ = crm_sync.build_report_payload(
        _mission(), result, "Construction Progress Report")
    vols = section_data["volumetrics"]["measurements"]
    assert vols[0]["type"] == "fill" and vols[0]["value"] == 120.5
    assert section_data["change_detection"]["comparison_date"] == "2026-06-01"
    assert "detection_heatmap" not in section_data


def test_push_report_end_to_end(configured, monkeypatch, tmp_path):
    result = _veg_result(tmp_path)
    posts = []

    def fake_get(url, headers=None, params=None, timeout=None):
        assert "report_templates" in url
        return FakeResponse([{"id": "tpl-1", "name": "Vegetation Analysis Report",
                              "sections_manifest": []}])

    def fake_post(url, headers=None, params=None, json=None, data=None, timeout=None):
        posts.append({"url": url, "json": json})
        if "job_reports" in url:
            return FakeResponse([{"id": "rep-1"}])
        return FakeResponse()

    monkeypatch.setattr(crm_sync.requests, "get", fake_get)
    monkeypatch.setattr(crm_sync.requests, "post", fake_post)

    report_id = crm_sync.push_report(_mission(), result)
    assert report_id == "rep-1"

    report_post = next(p for p in posts if "job_reports" in p["url"])
    assert report_post["json"]["job_id"] == SAMPLE_ROW["id"]
    assert report_post["json"]["template_id"] == "tpl-1"
    assert report_post["json"]["status"] == "draft"

    storage_posts = [p for p in posts if "/storage/v1/object/media/" in p["url"]]
    assert len(storage_posts) == 3  # heatmap + ortho preview + photo thumb

    image_rows_post = next(p for p in posts if "report_images" in p["url"])
    rows = image_rows_post["json"]
    assert len(rows) == 3
    assert all(r["report_id"] == "rep-1" for r in rows)
    assert all(r["image_url"].startswith(
        "https://example.supabase.co/storage/v1/object/public/media/report-images/rep-1/")
        for r in rows)


def test_push_report_skips_unmapped_job_type(configured, tmp_path):
    result = _veg_result(tmp_path)
    result["report_data"]["job_type"] = "gaussian_splat"
    assert crm_sync.push_report(_mission(), result) is None


def test_push_report_never_raises(configured, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(crm_sync.requests, "get", boom)
    assert crm_sync.push_report(_mission(), _veg_result(tmp_path)) is None
