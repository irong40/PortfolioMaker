"""Tests for cloudcompare_ops -- CloudCompare CLI wrapper module."""

import os
import shutil
import struct
import subprocess

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_ascii_ply(tmp_path):
    """Create a minimal ASCII PLY file with known geometry."""
    ply_content = (
        "ply\n"
        "format ascii 1.0\n"
        "element vertex 8\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
        "0.0 0.0 0.0\n"
        "10.0 0.0 0.0\n"
        "10.0 10.0 0.0\n"
        "0.0 10.0 0.0\n"
        "0.0 0.0 5.0\n"
        "10.0 0.0 5.0\n"
        "10.0 10.0 5.0\n"
        "0.0 10.0 5.0\n"
    )
    path = tmp_path / "test_cloud.ply"
    path.write_text(ply_content)
    return str(path)


@pytest.fixture
def sample_binary_ply(tmp_path):
    """Create a minimal binary little-endian PLY file."""
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "element vertex 4\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "end_header\n"
    )
    path = tmp_path / "test_binary.ply"
    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        for point in [(0, 0, 0), (5, 0, 0), (5, 5, 0), (0, 5, 3)]:
            f.write(struct.pack("<fff", *[float(v) for v in point]))
    return str(path)


@pytest.fixture
def sample_volume_result():
    """A realistic volume calculation result dict."""
    return {
        "volume_m3": 1523.47,
        "surface_area_m2": 892.15,
        "grid_step": 0.5,
        "point_count": 245000,
        "bbox": {
            "min_x": 364521.12,
            "min_y": 4078234.56,
            "min_z": 12.34,
            "max_x": 364571.12,
            "max_y": 4078284.56,
            "max_z": 18.92,
        },
        "output_report": "volume_report.txt",
    }


CC_EXE = r"C:\Program Files\CloudCompare\CloudCompare.exe"


# ---------------------------------------------------------------------------
# find_cloudcompare tests
# ---------------------------------------------------------------------------

class TestFindCloudCompare:
    def test_finds_in_common_path(self, monkeypatch):
        """Should return path when CC is at a common install location."""
        from cloudcompare_ops import find_cloudcompare, _CC_SEARCH_PATHS

        found_path = _CC_SEARCH_PATHS[0]
        monkeypatch.setattr(os.path, "isfile", lambda p: p == found_path)

        result = find_cloudcompare()
        assert result == found_path

    def test_finds_in_system_path(self, monkeypatch):
        """Should find CC via 'where' command on Windows."""
        monkeypatch.setattr(os.path, "isfile", lambda p: False)

        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout="C:\\Custom\\CloudCompare\\CloudCompare.exe\n",
        )
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: fake_result,
        )

        from cloudcompare_ops import find_cloudcompare
        result = find_cloudcompare()
        assert "CloudCompare" in result

    def test_raises_when_not_found(self, monkeypatch):
        """Should raise FileNotFoundError with download URL."""
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        monkeypatch.setattr(
            subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="",
            ),
        )

        from cloudcompare_ops import find_cloudcompare
        with pytest.raises(FileNotFoundError, match="danielgm.net"):
            find_cloudcompare()


# ---------------------------------------------------------------------------
# calculate_volume tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestCalculateVolume:
    def test_builds_correct_command(self, monkeypatch, sample_ascii_ply, tmp_path):
        """Verify the CLI command includes the right flags."""
        captured_args = {}

        def mock_run(cmd, **kwargs):
            captured_args["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=(
                    "Volume: 1234.56\n"
                    "Surface: 567.89\n"
                    "12345 points\n"
                    "Bounding box: [0.0,0.0,0.0] - [10.0,10.0,5.0]\n"
                ),
                stderr="",
            )

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = cloudcompare_ops.calculate_volume(
            sample_ascii_ply,
            grid_step=0.25,
            output_dir=str(tmp_path),
        )

        cmd = captured_args["cmd"]
        assert cmd[0] == CC_EXE
        assert "-SILENT" in cmd
        assert "-O" in cmd
        assert "-VOLUME" in cmd
        assert "-GRID_STEP" in cmd
        assert "0.25" in cmd

        assert result["volume_m3"] == pytest.approx(1234.56)
        assert result["surface_area_m2"] == pytest.approx(567.89)
        assert result["point_count"] == 12345
        assert result["grid_step"] == 0.25
        assert result["bbox"]["min_x"] == 0.0
        assert result["bbox"]["max_x"] == 10.0

    def test_flat_ground_level(self, monkeypatch, sample_ascii_ply, tmp_path):
        """Should add -CONST_HEIGHT 0 when ground_level='flat'."""
        captured_args = {}

        def mock_run(cmd, **kwargs):
            captured_args["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Volume: 100.0\n1000 points\n",
                stderr="",
            )

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        cloudcompare_ops.calculate_volume(
            sample_ascii_ply, ground_level="flat", output_dir=str(tmp_path),
        )

        cmd = captured_args["cmd"]
        assert "-CONST_HEIGHT" in cmd
        assert "0" in cmd

    def test_numeric_ground_level(self, monkeypatch, sample_ascii_ply, tmp_path):
        """Should pass numeric ground level via -CONST_HEIGHT."""
        captured_args = {}

        def mock_run(cmd, **kwargs):
            captured_args["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Volume: 50.0\n500 points\n",
                stderr="",
            )

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        cloudcompare_ops.calculate_volume(
            sample_ascii_ply, ground_level="12.5", output_dir=str(tmp_path),
        )

        cmd = captured_args["cmd"]
        assert "-CONST_HEIGHT" in cmd
        assert "12.5" in cmd

    def test_writes_report_file(self, monkeypatch, sample_ascii_ply, tmp_path):
        """Should write a volume_report.txt to output dir."""
        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Volume: 99.9\n100 points\n",
                stderr="",
            )

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = cloudcompare_ops.calculate_volume(
            sample_ascii_ply, output_dir=str(tmp_path),
        )

        assert os.path.isfile(result["output_report"])
        content = open(result["output_report"]).read()
        assert "99.9" in content

    def test_missing_input_file(self, tmp_path):
        """Should raise FileNotFoundError for missing input."""
        from cloudcompare_ops import calculate_volume
        with pytest.raises(FileNotFoundError, match="Input cloud not found"):
            calculate_volume(str(tmp_path / "nonexistent.las"))


# ---------------------------------------------------------------------------
# compute_m3c2_distance tests (mocked subprocess)
# ---------------------------------------------------------------------------

class TestComputeM3C2:
    def test_builds_correct_command(self, monkeypatch, sample_ascii_ply, tmp_path):
        """Verify M3C2 CLI command includes both clouds and params file."""
        captured_args = {}

        def mock_run(cmd, **kwargs):
            captured_args["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=(
                    "Mean distance: 0.342\n"
                    "Std. dev.: 0.156\n"
                    "8 points\n"
                ),
                stderr="",
            )

        cloud2 = str(tmp_path / "cloud2.ply")
        shutil.copy(sample_ascii_ply, cloud2)

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = cloudcompare_ops.compute_m3c2_distance(
            sample_ascii_ply, cloud2,
            normal_scale=2.0, projection_scale=1.5,
            output_dir=str(tmp_path),
        )

        cmd = captured_args["cmd"]
        assert "-M3C2" in cmd
        assert cmd.count("-O") == 2
        assert "-SAVE_CLOUDS" in cmd

        assert result["mean_distance"] == pytest.approx(0.342)
        assert result["std_distance"] == pytest.approx(0.156)
        assert result["point_count"] == 8

    def test_generates_params_file(self, monkeypatch, sample_ascii_ply, tmp_path):
        """Should create m3c2_params.txt with correct scales."""
        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout="Mean dist: 0.1\nStd dev: 0.05\n10 points\n",
                stderr="",
            )

        cloud2 = str(tmp_path / "cloud2.ply")
        shutil.copy(sample_ascii_ply, cloud2)

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        cloudcompare_ops.compute_m3c2_distance(
            sample_ascii_ply, cloud2,
            normal_scale=3.0, projection_scale=2.0,
            output_dir=str(tmp_path),
        )

        params_path = tmp_path / "m3c2_params.txt"
        assert params_path.exists()
        content = params_path.read_text()
        assert "NormalScale=3.0" in content
        assert "ProjScale=2.0" in content

    def test_missing_cloud_raises(self, tmp_path, sample_ascii_ply):
        """Should raise for missing second cloud."""
        from cloudcompare_ops import compute_m3c2_distance
        with pytest.raises(FileNotFoundError):
            compute_m3c2_distance(sample_ascii_ply, str(tmp_path / "missing.las"))


# ---------------------------------------------------------------------------
# get_cloud_info tests
# ---------------------------------------------------------------------------

class TestGetCloudInfo:
    def test_ascii_ply_direct_parse(self, sample_ascii_ply):
        """Should parse ASCII PLY without needing CloudCompare."""
        from cloudcompare_ops import get_cloud_info
        result = get_cloud_info(sample_ascii_ply)

        assert result["point_count"] == 8
        assert result["format"] == "PLY"
        assert result["file_size_mb"] >= 0  # tiny file, may round small
        assert result["bbox"]["min_x"] == 0.0
        assert result["bbox"]["max_x"] == 10.0
        assert result["bbox"]["min_z"] == 0.0
        assert result["bbox"]["max_z"] == 5.0
        assert result["dimensions"]["x"] == 10.0
        assert result["dimensions"]["y"] == 10.0
        assert result["dimensions"]["z"] == 5.0

    def test_binary_ply_direct_parse(self, sample_binary_ply):
        """Should parse binary PLY header and compute bbox."""
        from cloudcompare_ops import get_cloud_info
        result = get_cloud_info(sample_binary_ply)

        assert result["point_count"] == 4
        assert result["format"] == "PLY"
        assert result["bbox"]["min_x"] == 0.0
        assert result["bbox"]["max_x"] == 5.0
        assert result["bbox"]["max_z"] == 3.0

    def test_missing_file_raises(self, tmp_path):
        """Should raise for nonexistent file."""
        from cloudcompare_ops import get_cloud_info
        with pytest.raises(FileNotFoundError):
            get_cloud_info(str(tmp_path / "nonexistent.las"))

    def test_non_ply_falls_back_to_cloudcompare(self, monkeypatch, tmp_path):
        """For non-PLY formats, should try CloudCompare CLI."""
        las_file = tmp_path / "test.las"
        las_file.write_bytes(b"fake las content for testing")

        def mock_run(cmd, **kwargs):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0,
                stdout=(
                    "50000 points\n"
                    "Bounding box: [100.0,200.0,10.0] - [150.0,250.0,25.0]\n"
                ),
                stderr="",
            )

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", lambda: CC_EXE)
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = cloudcompare_ops.get_cloud_info(str(las_file))

        assert result["point_count"] == 50000
        assert result["format"] == "LAS"
        assert result["bbox"]["min_x"] == 100.0

    def test_graceful_when_cc_missing(self, monkeypatch, tmp_path):
        """Should return partial info when CC is not installed and file is not PLY."""
        las_file = tmp_path / "test.las"
        las_file.write_bytes(b"fake las data")

        def raise_not_found():
            raise FileNotFoundError("not installed")

        import cloudcompare_ops
        monkeypatch.setattr(cloudcompare_ops, "find_cloudcompare", raise_not_found)

        result = cloudcompare_ops.get_cloud_info(str(las_file))

        assert result["format"] == "LAS"
        assert result["file_size_mb"] >= 0
        assert result["point_count"] == 0


# ---------------------------------------------------------------------------
# generate_volume_report PDF tests
# ---------------------------------------------------------------------------

class TestGenerateVolumeReport:
    def test_creates_pdf(self, sample_volume_result, tmp_path):
        """Should generate a valid PDF file."""
        from cloudcompare_ops import generate_volume_report
        pdf_path = generate_volume_report(
            sample_volume_result,
            site_name="Test Construction Site",
            output_dir=str(tmp_path),
            date="2026-04-05",
        )

        assert os.path.isfile(pdf_path)
        assert pdf_path.endswith(".pdf")

        with open(pdf_path, "rb") as f:
            header = f.read(5)
        assert header == b"%PDF-"

    def test_pdf_filename_includes_site(self, sample_volume_result, tmp_path):
        """Filename should include sanitized site name and date."""
        from cloudcompare_ops import generate_volume_report
        pdf_path = generate_volume_report(
            sample_volume_result,
            site_name="My Site / Phase 2",
            output_dir=str(tmp_path),
            date="2026-04-05",
        )

        filename = os.path.basename(pdf_path)
        assert "My_Site" in filename
        assert "2026-04-05" in filename

    def test_minimal_volume_result(self, tmp_path):
        """Should handle a minimal result dict (just volume_m3)."""
        from cloudcompare_ops import generate_volume_report
        pdf_path = generate_volume_report(
            {"volume_m3": 42.0},
            site_name="Minimal Test",
            output_dir=str(tmp_path),
        )

        assert os.path.isfile(pdf_path)
        assert os.path.getsize(pdf_path) > 1000

    def test_default_date_is_today(self, sample_volume_result, tmp_path):
        """Should use today's date when no date is provided."""
        from cloudcompare_ops import generate_volume_report
        from datetime import datetime

        pdf_path = generate_volume_report(
            sample_volume_result,
            site_name="Date Test",
            output_dir=str(tmp_path),
        )

        today = datetime.now().strftime("%Y-%m-%d")
        assert today in os.path.basename(pdf_path)

    def test_creates_output_dir(self, sample_volume_result, tmp_path):
        """Should create the output directory if it does not exist."""
        from cloudcompare_ops import generate_volume_report
        nested_dir = str(tmp_path / "reports" / "volume")

        pdf_path = generate_volume_report(
            sample_volume_result,
            site_name="Nested Dir Test",
            output_dir=nested_dir,
        )

        assert os.path.isdir(nested_dir)
        assert os.path.isfile(pdf_path)


# ---------------------------------------------------------------------------
# _parse_ply_header edge cases
# ---------------------------------------------------------------------------

class TestParsePlyHeader:
    def test_empty_file(self, tmp_path):
        """Should return None for empty file."""
        from cloudcompare_ops import _parse_ply_header
        empty = tmp_path / "empty.ply"
        empty.write_bytes(b"")
        assert _parse_ply_header(str(empty)) is None

    def test_no_vertices(self, tmp_path):
        """Should return None for PLY with 0 vertices."""
        from cloudcompare_ops import _parse_ply_header
        ply = tmp_path / "no_verts.ply"
        ply.write_text(
            "ply\nformat ascii 1.0\nelement vertex 0\nend_header\n"
        )
        assert _parse_ply_header(str(ply)) is None

    def test_header_only_no_xyz(self, tmp_path):
        """Should return point count but empty bbox when no x/y/z properties."""
        from cloudcompare_ops import _parse_ply_header
        ply = tmp_path / "no_xyz.ply"
        ply.write_text(
            "ply\nformat ascii 1.0\n"
            "element vertex 10\n"
            "property float intensity\n"
            "end_header\n"
            + "1.0\n" * 10
        )
        result = _parse_ply_header(str(ply))
        assert result is not None
        assert result["point_count"] == 10
        assert result["bbox"] == {}
