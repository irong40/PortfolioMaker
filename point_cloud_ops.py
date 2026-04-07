"""
Sortie — Point Cloud & Mesh Operations

Three core capabilities powered by Open3D:
1. Point cloud comparison — ICP alignment + cloud-to-cloud distance
2. Mesh cleanup — denoise, decimate, remove degenerates, fill holes
3. Volume calculation — cut/fill from DSM pairs, mesh volume

All functions return result dicts and fall back gracefully
when Open3D is not installed.
"""

import os
import logging
import numpy as np
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import open3d as o3d
    OPEN3D_AVAILABLE = True
except ImportError:
    OPEN3D_AVAILABLE = False


# ─── POINT CLOUD I/O ────────────────────────────────────────────────────

def _load_point_cloud(path):
    """Load a point cloud from PLY, LAS/LAZ, PCD, or XYZ.

    For LAS/LAZ, uses laspy if available, otherwise Open3D native.
    """
    path = str(path)
    ext = Path(path).suffix.lower()

    if ext in (".las", ".laz"):
        try:
            import laspy
            las = laspy.read(path)
            points = np.vstack([las.x, las.y, las.z]).T
            pcd = o3d.geometry.PointCloud()
            pcd.points = o3d.utility.Vector3dVector(points)
            if hasattr(las, 'red') and hasattr(las, 'green') and hasattr(las, 'blue'):
                colors = np.vstack([las.red, las.green, las.blue]).T / 65535.0
                pcd.colors = o3d.utility.Vector3dVector(colors)
            log.info(f"Loaded {len(pcd.points)} points from {path} via laspy")
            return pcd
        except ImportError:
            log.warning("laspy not installed — trying Open3D native LAS reader")

    pcd = o3d.io.read_point_cloud(path)
    log.info(f"Loaded {len(pcd.points)} points from {path}")
    return pcd


def _load_mesh(path):
    """Load a triangle mesh from PLY, OBJ, STL, or OFF."""
    mesh = o3d.io.read_triangle_mesh(str(path))
    mesh.compute_vertex_normals()
    log.info(f"Loaded mesh: {len(mesh.vertices)} vertices, {len(mesh.triangles)} triangles")
    return mesh


def _load_dsm_as_cloud(dsm_path):
    """Load a GeoTIFF DSM as a point cloud (X=col, Y=row, Z=elevation).

    Returns Open3D PointCloud with real-world coordinates if rasterio
    is available, otherwise pixel coordinates.
    """
    try:
        import rasterio
        with rasterio.open(dsm_path) as src:
            dem = src.read(1)
            transform = src.transform
            nodata = src.nodata

        rows, cols = np.where(dem != nodata if nodata is not None else np.ones_like(dem, dtype=bool))
        xs, ys = rasterio.transform.xy(transform, rows, cols)
        zs = dem[rows, cols]

        points = np.column_stack([xs, ys, zs]).astype(np.float64)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(points)
        log.info(f"Loaded DSM as cloud: {len(points)} points from {dsm_path}")
        return pcd, transform, dem.shape

    except ImportError:
        from PIL import Image
        img = Image.open(dsm_path)
        dem = np.array(img, dtype=np.float32)
        rows, cols = np.where(dem > -9000)
        points = np.column_stack([cols, rows, dem[rows, cols]]).astype(np.float64)
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(points)
        log.info(f"Loaded DSM as cloud (no georef): {len(points)} points")
        return pcd, None, dem.shape


# ─── 1. POINT CLOUD COMPARISON ──────────────────────────────────────────

def compare_clouds(source_path, target_path, voxel_size=0.1, max_correspondence=1.0):
    """Compare two point clouds: align with ICP then compute distances.

    Args:
        source_path: Path to current visit cloud (PLY, LAS, PCD)
        target_path: Path to previous visit cloud
        voxel_size: Downsample voxel size in meters (0 = no downsampling)
        max_correspondence: ICP max correspondence distance in meters

    Returns:
        dict with keys:
            mean_distance: average point-to-point distance after alignment
            max_distance: maximum distance
            std_distance: standard deviation
            changed_points_pct: % of points with distance > 2*std
            rmse: root mean square error of alignment
            num_points_source: points in source
            num_points_target: points in target
            distances: numpy array of per-point distances
            transformation: 4x4 alignment matrix
        OR None if Open3D unavailable.
    """
    if not OPEN3D_AVAILABLE:
        log.warning("Open3D not installed — skipping cloud comparison")
        return None

    try:
        source = _load_point_cloud(source_path)
        target = _load_point_cloud(target_path)

        if len(source.points) == 0 or len(target.points) == 0:
            log.error("Empty point cloud — cannot compare")
            return None

        # Downsample for speed
        if voxel_size > 0:
            source_ds = source.voxel_down_sample(voxel_size)
            target_ds = target.voxel_down_sample(voxel_size)
        else:
            source_ds = source
            target_ds = target

        # Estimate normals for point-to-plane ICP
        for pcd in [source_ds, target_ds]:
            pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(
                radius=voxel_size * 4 if voxel_size > 0 else 0.5,
                max_nn=30,
            ))

        # ICP alignment
        icp_result = o3d.pipelines.registration.registration_icp(
            source_ds, target_ds,
            max_correspondence_distance=max_correspondence,
            estimation_method=o3d.pipelines.registration.TransformationEstimationPointToPlane(),
        )

        # Apply transformation and compute distances
        source_aligned = source_ds.transform(icp_result.transformation)
        distances = np.asarray(source_aligned.compute_point_cloud_distance(target_ds))

        mean_dist = float(np.mean(distances))
        std_dist = float(np.std(distances))
        changed_mask = distances > (mean_dist + 2 * std_dist)

        result = {
            "mean_distance": mean_dist,
            "max_distance": float(np.max(distances)),
            "std_distance": std_dist,
            "changed_points_pct": float(np.sum(changed_mask) / len(distances) * 100),
            "rmse": float(icp_result.inlier_rmse),
            "fitness": float(icp_result.fitness),
            "num_points_source": len(source_ds.points),
            "num_points_target": len(target_ds.points),
            "distances": distances,
            "transformation": np.asarray(icp_result.transformation),
        }

        log.info(f"Cloud comparison: mean={mean_dist:.3f}m, "
                 f"changed={result['changed_points_pct']:.1f}%, "
                 f"fitness={result['fitness']:.3f}")
        return result

    except Exception as e:
        log.error(f"Cloud comparison failed: {e}")
        return None


def compare_dsms(current_dsm_path, previous_dsm_path):
    """Compare two DSM GeoTIFFs and compute volumetric change.

    This is the primary method for construction progress tracking.

    Args:
        current_dsm_path: Path to current visit DSM
        previous_dsm_path: Path to previous visit DSM

    Returns:
        dict with keys:
            cut_volume_m3: volume removed (negative change)
            fill_volume_m3: volume added (positive change)
            net_volume_m3: net change (fill - cut)
            mean_change_m: average elevation change
            max_rise_m: maximum elevation increase
            max_drop_m: maximum elevation decrease
            changed_area_pct: % of area with >0.1m change
            change_map: 2D numpy array of elevation differences
            pixel_area_m2: area per pixel
        OR None if unavailable.
    """
    if not OPEN3D_AVAILABLE:
        log.warning("Open3D not installed — skipping DSM comparison")
        return None

    try:
        try:
            import rasterio
            has_rasterio = True
        except ImportError:
            has_rasterio = False

        if has_rasterio:
            import rasterio
            from rasterio.warp import reproject, Resampling

            with rasterio.open(current_dsm_path) as src:
                current = src.read(1)
                transform = src.transform
                crs = src.crs
                nodata = src.nodata
                pixel_w = abs(transform.a)
                pixel_h = abs(transform.e)

            with rasterio.open(previous_dsm_path) as src:
                prev_raw = src.read(1)
                prev_transform = src.transform
                prev_crs = src.crs
                prev_nodata = src.nodata

            # Reproject previous to match current grid if needed
            if prev_raw.shape != current.shape or prev_transform != transform:
                previous = np.empty_like(current)
                reproject(
                    prev_raw, previous,
                    src_transform=prev_transform, src_crs=prev_crs, src_nodata=prev_nodata,
                    dst_transform=transform, dst_crs=crs, dst_nodata=nodata,
                    resampling=Resampling.bilinear,
                )
            else:
                previous = prev_raw

            # Pixel area in square meters
            if crs and crs.is_projected:
                pixel_area = pixel_w * pixel_h  # already in meters
            elif crs and crs.is_geographic:
                # Geographic CRS — convert degrees to meters using center latitude
                center_lat = transform.f + transform.e * current.shape[0] / 2
                lat_rad = np.radians(center_lat)
                m_per_deg_lat = 111132.92 - 559.82 * np.cos(2 * lat_rad) + 1.175 * np.cos(4 * lat_rad)
                m_per_deg_lon = 111412.84 * np.cos(lat_rad) - 93.5 * np.cos(3 * lat_rad)
                pixel_area = abs(pixel_h * m_per_deg_lat) * abs(pixel_w * m_per_deg_lon)
            else:
                # No CRS — use pixel units
                pixel_area = pixel_w * pixel_h if pixel_w > 0 and pixel_h > 0 else 1.0

        else:
            from PIL import Image
            current = np.array(Image.open(current_dsm_path), dtype=np.float32)
            previous = np.array(Image.open(previous_dsm_path), dtype=np.float32)
            nodata = -9999
            pixel_area = 1.0  # unknown without georef

        # Mask nodata
        valid = np.ones_like(current, dtype=bool)
        if nodata is not None:
            valid &= (current != nodata) & (previous != nodata)
        valid &= (current > -9000) & (previous > -9000)

        change = np.where(valid, current - previous, 0)

        # Volume calculation (each pixel = pixel_area * height_change)
        fill_mask = change > 0.05  # 5cm threshold
        cut_mask = change < -0.05

        fill_volume = float(np.sum(change[fill_mask]) * pixel_area)
        cut_volume = float(abs(np.sum(change[cut_mask])) * pixel_area)

        valid_changes = change[valid]
        changed_area = np.sum(np.abs(valid_changes) > 0.1) / max(np.sum(valid), 1) * 100

        result = {
            "cut_volume_m3": cut_volume,
            "fill_volume_m3": fill_volume,
            "net_volume_m3": fill_volume - cut_volume,
            "mean_change_m": float(np.mean(valid_changes)) if len(valid_changes) > 0 else 0,
            "max_rise_m": float(np.max(valid_changes)) if len(valid_changes) > 0 else 0,
            "max_drop_m": float(np.min(valid_changes)) if len(valid_changes) > 0 else 0,
            "changed_area_pct": float(changed_area),
            "change_map": change,
            "pixel_area_m2": pixel_area,
        }

        log.info(f"DSM comparison: cut={cut_volume:.1f}m³, fill={fill_volume:.1f}m³, "
                 f"net={result['net_volume_m3']:.1f}m³, changed={changed_area:.1f}%")
        return result

    except Exception as e:
        log.error(f"DSM comparison failed: {e}")
        return None


# ─── 2. MESH CLEANUP ────────────────────────────────────────────────────

def cleanup_mesh(mesh_path, output_path=None,
                 target_triangles=None, smooth_iterations=3,
                 remove_small_components=True, min_component_ratio=0.01):
    """Clean up a photogrammetry mesh.

    Operations (in order):
    1. Remove degenerate triangles (zero-area, duplicate vertices)
    2. Remove small disconnected components (noise clusters)
    3. Smooth (Laplacian)
    4. Decimate to target triangle count (if specified)
    5. Recompute normals

    Args:
        mesh_path: Path to input mesh (PLY, OBJ, STL)
        output_path: Path to save cleaned mesh (None = don't save)
        target_triangles: Target triangle count for decimation (None = skip)
        smooth_iterations: Laplacian smoothing passes (0 = skip)
        remove_small_components: Remove disconnected noise clusters
        min_component_ratio: Min component size as fraction of largest

    Returns:
        dict with keys:
            vertices_before, vertices_after
            triangles_before, triangles_after
            components_removed
            mesh: the cleaned Open3D TriangleMesh object
            output_path: path to saved file (if saved)
        OR None if unavailable.
    """
    if not OPEN3D_AVAILABLE:
        log.warning("Open3D not installed — skipping mesh cleanup")
        return None

    try:
        mesh = _load_mesh(mesh_path)
        verts_before = len(mesh.vertices)
        tris_before = len(mesh.triangles)
        components_removed = 0

        # 1. Remove degenerate triangles
        mesh.remove_degenerate_triangles()
        mesh.remove_duplicated_triangles()
        mesh.remove_duplicated_vertices()
        mesh.remove_unreferenced_vertices()

        # 2. Remove small disconnected components
        if remove_small_components:
            triangle_clusters, cluster_n_triangles, _ = (
                mesh.cluster_connected_triangles()
            )
            triangle_clusters = np.asarray(triangle_clusters)
            cluster_n_triangles = np.asarray(cluster_n_triangles)

            if len(cluster_n_triangles) > 1:
                max_cluster = cluster_n_triangles.max()
                threshold = max_cluster * min_component_ratio
                keep_mask = cluster_n_triangles[triangle_clusters] >= threshold
                mesh.remove_triangles_by_mask(~keep_mask)
                mesh.remove_unreferenced_vertices()
                components_removed = int(np.sum(cluster_n_triangles < threshold))

        # 3. Smooth
        if smooth_iterations > 0:
            mesh = mesh.filter_smooth_laplacian(
                number_of_iterations=smooth_iterations,
            )

        # 4. Decimate
        if target_triangles and len(mesh.triangles) > target_triangles:
            mesh = mesh.simplify_quadric_decimation(
                target_number_of_triangles=target_triangles,
            )

        # 5. Recompute normals
        mesh.compute_vertex_normals()

        verts_after = len(mesh.vertices)
        tris_after = len(mesh.triangles)

        # Save if requested
        saved_path = None
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            o3d.io.write_triangle_mesh(str(output_path), mesh)
            saved_path = str(output_path)
            log.info(f"Cleaned mesh saved: {saved_path}")

        result = {
            "vertices_before": verts_before,
            "vertices_after": verts_after,
            "triangles_before": tris_before,
            "triangles_after": tris_after,
            "components_removed": components_removed,
            "reduction_pct": (1 - tris_after / max(tris_before, 1)) * 100,
            "mesh": mesh,
            "output_path": saved_path,
        }

        log.info(f"Mesh cleanup: {tris_before} → {tris_after} triangles "
                 f"({result['reduction_pct']:.1f}% reduction), "
                 f"{components_removed} components removed")
        return result

    except Exception as e:
        log.error(f"Mesh cleanup failed: {e}")
        return None


def get_mesh_stats(mesh_path):
    """Get basic mesh statistics without modifying.

    Returns:
        dict with vertices, triangles, bounds, surface_area,
        is_watertight, num_components
        OR None.
    """
    if not OPEN3D_AVAILABLE:
        return None

    try:
        mesh = _load_mesh(mesh_path)

        # Bounding box
        bbox = mesh.get_axis_aligned_bounding_box()
        extent = bbox.get_extent()

        # Connected components
        triangle_clusters, cluster_n_triangles, _ = (
            mesh.cluster_connected_triangles()
        )

        # Surface area
        surface_area = mesh.get_surface_area()

        return {
            "vertices": len(mesh.vertices),
            "triangles": len(mesh.triangles),
            "extent_x": float(extent[0]),
            "extent_y": float(extent[1]),
            "extent_z": float(extent[2]),
            "surface_area": float(surface_area),
            "is_watertight": mesh.is_watertight(),
            "num_components": len(np.asarray(cluster_n_triangles)),
        }

    except Exception as e:
        log.error(f"Mesh stats failed: {e}")
        return None


# ─── 3. VOLUME CALCULATION ──────────────────────────────────────────────

def compute_mesh_volume(mesh_path):
    """Compute volume of a closed (watertight) mesh.

    Args:
        mesh_path: Path to mesh file

    Returns:
        dict with volume_m3, is_watertight, surface_area_m2
        OR None.
    """
    if not OPEN3D_AVAILABLE:
        return None

    try:
        mesh = _load_mesh(mesh_path)
        watertight = mesh.is_watertight()

        if watertight:
            volume = mesh.get_volume()
        else:
            # Try to make watertight via convex hull for approximate volume
            hull, _ = mesh.compute_convex_hull()
            volume = hull.get_volume()
            log.warning("Mesh not watertight — using convex hull for approximate volume")

        return {
            "volume_m3": float(volume),
            "is_watertight": watertight,
            "surface_area_m2": float(mesh.get_surface_area()),
            "approximate": not watertight,
        }

    except Exception as e:
        log.error(f"Volume calculation failed: {e}")
        return None


def compute_stockpile_volume(cloud_path, ground_level=None, voxel_size=0.05):
    """Compute volume of a stockpile from a point cloud.

    Uses 2.5D grid projection: for each XY cell, volume = (Z - ground) * cell_area.

    Args:
        cloud_path: Path to point cloud
        ground_level: Ground elevation (auto-detect if None)
        voxel_size: Grid cell size in meters

    Returns:
        dict with volume_m3, base_area_m2, max_height_m, ground_level
        OR None.
    """
    if not OPEN3D_AVAILABLE:
        return None

    try:
        pcd = _load_point_cloud(cloud_path)
        points = np.asarray(pcd.points)

        if len(points) == 0:
            return None

        # Auto-detect ground level (lowest 5th percentile)
        if ground_level is None:
            ground_level = float(np.percentile(points[:, 2], 5))

        # Project to 2.5D grid
        x_min, y_min = points[:, 0].min(), points[:, 1].min()
        x_max, y_max = points[:, 0].max(), points[:, 1].max()

        nx = max(1, int((x_max - x_min) / voxel_size))
        ny = max(1, int((y_max - y_min) / voxel_size))

        # Bin points into grid cells, take max Z per cell
        xi = np.clip(((points[:, 0] - x_min) / voxel_size).astype(int), 0, nx - 1)
        yi = np.clip(((points[:, 1] - y_min) / voxel_size).astype(int), 0, ny - 1)

        grid = np.full((nx, ny), ground_level)
        for i in range(len(points)):
            grid[xi[i], yi[i]] = max(grid[xi[i], yi[i]], points[i, 2])

        # Volume = sum of (height above ground * cell area)
        heights = grid - ground_level
        heights = np.clip(heights, 0, None)  # Only above-ground volume
        cell_area = voxel_size * voxel_size
        volume = float(np.sum(heights) * cell_area)
        base_area = float(np.sum(heights > 0.05) * cell_area)

        return {
            "volume_m3": volume,
            "base_area_m2": base_area,
            "max_height_m": float(np.max(heights)),
            "ground_level": ground_level,
            "grid_cells": nx * ny,
        }

    except Exception as e:
        log.error(f"Stockpile volume failed: {e}")
        return None


# ─── CHANGE MAP VISUALIZATION ───────────────────────────────────────────

def save_change_map(change_map, output_path, vmin=-2, vmax=2):
    """Save a DSM change map as a colorized JPEG for report embedding.

    Blue = cut (lowered), Red = fill (raised), White = no change.

    Args:
        change_map: 2D numpy array of elevation differences
        output_path: Path to save JPEG
        vmin/vmax: color scale range in meters

    Returns:
        Path to saved file, or None.
    """
    try:
        from PIL import Image

        # Normalize to 0-255
        normalized = np.clip((change_map - vmin) / (vmax - vmin), 0, 1)

        # Blue (cut) → White (no change) → Red (fill)
        r = (normalized * 255).astype(np.uint8)
        g = (255 - np.abs(normalized - 0.5) * 2 * 255).astype(np.uint8)
        b = ((1 - normalized) * 255).astype(np.uint8)

        rgb = np.stack([r, g, b], axis=-1)
        img = Image.fromarray(rgb)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        img.save(str(output_path), format="JPEG", quality=90)
        log.info(f"Change map saved: {output_path}")
        return str(output_path)

    except Exception as e:
        log.error(f"Change map save failed: {e}")
        return None


# ─── PREVIOUS VISIT DETECTION ───────────────────────────────────────────

def find_previous_visit(output_dir, site_name, current_date):
    """Find the most recent previous visit for a site.

    Looks in the Portfolio folder structure:
    E:\\Portfolio\\{site_name}\\{date}\\{job_type}\\

    Args:
        output_dir: Current output directory
        site_name: Site name
        current_date: Current date string (YYYY-MM-DD)

    Returns:
        dict with previous_dir, previous_date, previous_dsm, previous_cloud
        OR None if no previous visit found.
    """
    output_path = Path(output_dir)

    # Navigate up to site root (above date folder)
    site_root = output_path.parent.parent
    if not site_root.exists():
        return None

    # Find all date folders
    date_dirs = []
    for d in site_root.iterdir():
        if d.is_dir() and d.name != current_date and len(d.name) == 10:
            try:
                # Validate date format
                parts = d.name.split("-")
                if len(parts) == 3:
                    date_dirs.append(d)
            except (ValueError, IndexError):
                continue

    if not date_dirs:
        return None

    # Sort by date (newest first) and find one with matching job type
    date_dirs.sort(key=lambda d: d.name, reverse=True)
    job_type = output_path.name

    for prev_dir in date_dirs:
        prev_job_dir = prev_dir / job_type
        if prev_job_dir.exists():
            result = {
                "previous_dir": str(prev_job_dir),
                "previous_date": prev_dir.name,
            }

            # Look for DSM
            dsm = prev_job_dir / "dsm.tif"
            if dsm.exists():
                result["previous_dsm"] = str(dsm)

            # Look for point cloud
            for ext in ["georeferenced_model.laz", "point_cloud.ply", "cloud.ply"]:
                cloud = prev_job_dir / ext
                if cloud.exists():
                    result["previous_cloud"] = str(cloud)
                    break

            # Look for mesh
            mesh_zip = prev_job_dir / "textured_model.zip"
            if mesh_zip.exists():
                result["previous_mesh"] = str(mesh_zip)

            log.info(f"Found previous visit: {prev_dir.name} at {prev_job_dir}")
            return result

    return None
