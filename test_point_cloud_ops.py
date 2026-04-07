"""Tests for point_cloud_ops — point cloud comparison, mesh cleanup, volume calculation."""

import os
import pytest
import numpy as np

try:
    import open3d as o3d
    OPEN3D_AVAILABLE = True
except ImportError:
    OPEN3D_AVAILABLE = False


# ─── FIXTURES ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_cloud(tmp_path):
    """Create a simple point cloud PLY file."""
    if not OPEN3D_AVAILABLE:
        pytest.skip("Open3D not installed")
    np.random.seed(42)
    points = np.random.uniform(-10, 10, (5000, 3))
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    path = str(tmp_path / "cloud.ply")
    o3d.io.write_point_cloud(path, pcd)
    return path


@pytest.fixture
def shifted_cloud(tmp_path):
    """Create a second cloud shifted slightly from sample_cloud."""
    if not OPEN3D_AVAILABLE:
        pytest.skip("Open3D not installed")
    np.random.seed(42)
    points = np.random.uniform(-10, 10, (5000, 3))
    # Apply small shift + add some changed points
    points[:, 2] += 0.3  # lift everything 0.3m
    points[:500, 2] += 2.0  # add a "stockpile" to first 500 points
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    path = str(tmp_path / "cloud_shifted.ply")
    o3d.io.write_point_cloud(path, pcd)
    return path


@pytest.fixture
def sample_mesh(tmp_path):
    """Create a simple box mesh."""
    if not OPEN3D_AVAILABLE:
        pytest.skip("Open3D not installed")
    mesh = o3d.geometry.TriangleMesh.create_box(width=5, height=3, depth=2)
    mesh.compute_vertex_normals()
    path = str(tmp_path / "mesh.ply")
    o3d.io.write_triangle_mesh(path, mesh)
    return path


@pytest.fixture
def noisy_mesh(tmp_path):
    """Create a mesh with noise and small disconnected components."""
    if not OPEN3D_AVAILABLE:
        pytest.skip("Open3D not installed")
    # Main mesh
    main = o3d.geometry.TriangleMesh.create_sphere(radius=5)
    main.compute_vertex_normals()

    # Add small noise cluster far away
    noise = o3d.geometry.TriangleMesh.create_sphere(radius=0.1)
    noise.translate([50, 50, 50])
    noise.compute_vertex_normals()

    combined = main + noise
    path = str(tmp_path / "noisy_mesh.ply")
    o3d.io.write_triangle_mesh(path, combined)
    return path


@pytest.fixture
def dsm_pair(tmp_path):
    """Create two DSM GeoTIFFs with known elevation difference."""
    from PIL import Image

    np.random.seed(42)
    base = np.random.uniform(10, 15, (100, 100)).astype(np.float32)

    # Current DSM: base + a raised area in center
    current = base.copy()
    current[30:70, 30:70] += 3.0  # 3m fill in center

    # Previous DSM: just the base
    previous = base.copy()

    current_path = str(tmp_path / "dsm_current.tif")
    previous_path = str(tmp_path / "dsm_previous.tif")

    Image.fromarray(current, mode="F").save(current_path)
    Image.fromarray(previous, mode="F").save(previous_path)

    return current_path, previous_path


# ─── CLOUD COMPARISON TESTS ─────────────────────────────────────────────

@pytest.mark.skipif(not OPEN3D_AVAILABLE, reason="Open3D not installed")
class TestCompareClods:
    def test_identical_clouds_zero_distance(self, sample_cloud):
        from point_cloud_ops import compare_clouds
        result = compare_clouds(sample_cloud, sample_cloud, voxel_size=0.5)
        assert result is not None
        assert result["mean_distance"] < 0.01
        assert result["fitness"] > 0.9

    def test_shifted_clouds_detect_change(self, sample_cloud, shifted_cloud):
        from point_cloud_ops import compare_clouds
        result = compare_clouds(sample_cloud, shifted_cloud, voxel_size=0.5)
        assert result is not None
        # ICP partially aligns the shift, but max distance reveals the stockpile
        assert result["max_distance"] > 0.5
        assert result["num_points_source"] > 0
        assert result["num_points_target"] > 0
        assert "distances" in result
        assert "transformation" in result

    def test_returns_none_without_open3d(self, monkeypatch):
        import point_cloud_ops
        monkeypatch.setattr(point_cloud_ops, "OPEN3D_AVAILABLE", False)
        result = point_cloud_ops.compare_clouds("a.ply", "b.ply")
        assert result is None


# ─── DSM COMPARISON TESTS ───────────────────────────────────────────────

@pytest.mark.skipif(not OPEN3D_AVAILABLE, reason="Open3D not installed")
class TestCompareDSMs:
    def test_detects_fill_volume(self, dsm_pair):
        from point_cloud_ops import compare_dsms
        current, previous = dsm_pair
        result = compare_dsms(current, previous)
        assert result is not None
        # Center 40x40 area raised by 3m = ~4800 m³ (with pixel_area=1 for non-georef)
        assert result["fill_volume_m3"] > 0
        assert result["net_volume_m3"] > 0
        assert result["max_rise_m"] > 2.5
        assert result["changed_area_pct"] > 10

    def test_symmetric_comparison(self, dsm_pair):
        from point_cloud_ops import compare_dsms
        current, previous = dsm_pair
        # Swap: fill becomes cut
        result = compare_dsms(previous, current)
        assert result is not None
        assert result["cut_volume_m3"] > 0
        assert result["net_volume_m3"] < 0

    def test_identical_dsms_no_change(self, dsm_pair):
        from point_cloud_ops import compare_dsms
        current, _ = dsm_pair
        result = compare_dsms(current, current)
        assert result is not None
        assert abs(result["net_volume_m3"]) < 0.01
        assert result["changed_area_pct"] < 1


# ─── MESH CLEANUP TESTS ─────────────────────────────────────────────────

@pytest.mark.skipif(not OPEN3D_AVAILABLE, reason="Open3D not installed")
class TestMeshCleanup:
    def test_basic_cleanup(self, sample_mesh, tmp_path):
        from point_cloud_ops import cleanup_mesh
        out = str(tmp_path / "cleaned.ply")
        result = cleanup_mesh(sample_mesh, output_path=out)
        assert result is not None
        assert result["triangles_after"] > 0
        assert result["vertices_after"] > 0
        assert os.path.exists(out)

    def test_removes_small_components(self, tmp_path):
        """Build mesh with guaranteed separate components in-memory."""
        from point_cloud_ops import cleanup_mesh

        # Create two spheres far apart, save separately then combine
        main = o3d.geometry.TriangleMesh.create_sphere(radius=5)
        noise = o3d.geometry.TriangleMesh.create_icosahedron(radius=0.05)
        noise.translate([100, 100, 100])
        combined = main + noise

        path = str(tmp_path / "two_component.ply")
        o3d.io.write_triangle_mesh(path, combined, write_vertex_normals=False)

        result = cleanup_mesh(path, min_component_ratio=0.1)
        assert result is not None
        assert result["triangles_after"] < result["triangles_before"]

    def test_decimation(self, sample_mesh):
        from point_cloud_ops import cleanup_mesh
        result = cleanup_mesh(sample_mesh, target_triangles=6)
        assert result is not None
        assert result["triangles_after"] <= 6

    def test_no_smooth(self, sample_mesh):
        from point_cloud_ops import cleanup_mesh
        result = cleanup_mesh(sample_mesh, smooth_iterations=0)
        assert result is not None

    def test_mesh_stats(self, sample_mesh):
        from point_cloud_ops import get_mesh_stats
        stats = get_mesh_stats(sample_mesh)
        assert stats is not None
        assert stats["vertices"] == 8  # box has 8 vertices
        assert stats["triangles"] == 12  # box has 12 triangles
        assert stats["extent_x"] == pytest.approx(5.0, abs=0.1)
        assert stats["is_watertight"] is True


# ─── VOLUME CALCULATION TESTS ───────────────────────────────────────────

@pytest.mark.skipif(not OPEN3D_AVAILABLE, reason="Open3D not installed")
class TestVolumeCalculation:
    def test_box_volume(self, sample_mesh):
        from point_cloud_ops import compute_mesh_volume
        result = compute_mesh_volume(sample_mesh)
        assert result is not None
        assert result["is_watertight"] is True
        # Box 5x3x2 = 30 m³
        assert result["volume_m3"] == pytest.approx(30.0, rel=0.01)

    def test_stockpile_volume(self, tmp_path):
        from point_cloud_ops import compute_stockpile_volume

        # Create a hemisphere stockpile point cloud
        np.random.seed(42)
        n = 10000
        theta = np.random.uniform(0, 2 * np.pi, n)
        phi = np.random.uniform(0, np.pi / 2, n)
        r = 5.0 * np.cbrt(np.random.uniform(0, 1, n))
        x = r * np.sin(phi) * np.cos(theta)
        y = r * np.sin(phi) * np.sin(theta)
        z = r * np.cos(phi)

        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(np.column_stack([x, y, z]))
        path = str(tmp_path / "stockpile.ply")
        o3d.io.write_point_cloud(path, pcd)

        result = compute_stockpile_volume(path, ground_level=0, voxel_size=0.2)
        assert result is not None
        assert result["volume_m3"] > 0
        assert result["max_height_m"] > 3
        assert result["base_area_m2"] > 0


# ─── CHANGE MAP VISUALIZATION ───────────────────────────────────────────

class TestChangeMap:
    def test_save_change_map(self, tmp_path):
        from point_cloud_ops import save_change_map
        change = np.random.uniform(-2, 2, (100, 100)).astype(np.float32)
        out = str(tmp_path / "change.jpg")
        result = save_change_map(change, out)
        assert result is not None
        assert os.path.exists(out)
        from PIL import Image
        img = Image.open(out)
        assert img.size == (100, 100)


# ─── PREVIOUS VISIT DETECTION ───────────────────────────────────────────

class TestFindPreviousVisit:
    def test_finds_previous_dsm(self, tmp_path):
        from point_cloud_ops import find_previous_visit

        # Create folder structure
        site = tmp_path / "Portfolio" / "TestSite"
        prev = site / "2026-03-01" / "construction_progress"
        curr = site / "2026-04-01" / "construction_progress"
        prev.mkdir(parents=True)
        curr.mkdir(parents=True)
        (prev / "dsm.tif").write_bytes(b"fake")

        result = find_previous_visit(str(curr), "TestSite", "2026-04-01")
        assert result is not None
        assert result["previous_date"] == "2026-03-01"
        assert "previous_dsm" in result

    def test_returns_none_when_no_previous(self, tmp_path):
        from point_cloud_ops import find_previous_visit
        site = tmp_path / "Portfolio" / "TestSite"
        curr = site / "2026-04-01" / "construction_progress"
        curr.mkdir(parents=True)

        result = find_previous_visit(str(curr), "TestSite", "2026-04-01")
        assert result is None

    def test_picks_most_recent(self, tmp_path):
        from point_cloud_ops import find_previous_visit

        site = tmp_path / "Portfolio" / "TestSite"
        for date in ["2026-01-01", "2026-02-15", "2026-03-20"]:
            d = site / date / "construction_progress"
            d.mkdir(parents=True)
            (d / "dsm.tif").write_bytes(b"fake")

        curr = site / "2026-04-01" / "construction_progress"
        curr.mkdir(parents=True)

        result = find_previous_visit(str(curr), "TestSite", "2026-04-01")
        assert result["previous_date"] == "2026-03-20"
