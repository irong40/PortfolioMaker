"""
Sortie — OpenSplat Service

Drives a self-built OpenSplat CUDA container (in the WSL2 docker stack) for
Gaussian Splat generation, replacing the MipMap engine (free tier caps at 512
images and its CLI needs a rotating per-GUI-session token).

Pipeline contract mirrors mipmap_service.run_mipmap_pipeline:
    run_opensplat_pipeline(...) -> {returncode, working_dir, gs_ply_dir, gs_sog_dir}
gs_sog_dir is always None — OpenSplat emits a single splat.ply (+ cameras.json);
nothing downstream references model-gs-sog-tile/ (verified 2026-07-22).

Inputs come from a NodeODM run submitted with
    outputs=["opensfm/reconstruction.json","opensfm/image_list.txt"]
(the default all.zip does NOT contain opensfm/ — verified against Task.js in the
deployed NodeODM 2.2.4). image_list.txt holds ABSOLUTE container paths
(/var/www/data/<uuid>/images/<name>) keyed to basename shot ids; we rewrite them
to absolute in-container paths under the /work mount (verified 2026-07-22).
"""

import json
import logging
import re
import shutil
import subprocess
import zipfile
from pathlib import Path, PureWindowsPath

log = logging.getLogger(__name__)

# ─── CONSTANTS ────────────────────────────────────────────────────────────────

# Pinned image: OpenSplat commit 9fb62fd, patched for CUDA 12.8 / sm_120
# (base nvidia/cuda:12.8.1-devel-ubuntu22.04, libtorch 2.7.1+cu128).
# Built + kernel-verified on the RTX 5070 on 2026-07-22. Rebuilds must bump
# this tag — never :latest.
OPENSPLAT_IMAGE = "opensplat:9fb62fd-cu128"

# The nvidia base image's entrypoint is a shell wrapper — the binary must be
# invoked by explicit path or "--help" gets swallowed by bash (verified).
OPENSPLAT_BIN = "/code/build/opensplat"

CONTAINER_NAME = "opensplat"
CONTAINER_WORK = "/work"

# The NodeODM `outputs` override for splat tasks. REPLACES the default
# all.zip whitelist — the archive contains only these (a few MB of poses).
OPENSFM_OUTPUTS = [
    "opensfm/reconstruction.json",
    "opensfm/image_list.txt",
]

_STEP_RE = re.compile(r"Step\s+(\d+):\s*([\d.]+)")

# Failure taxonomy — matcher only, NO auto-retry. Keyed by stderr/stdout
# signature; values are the operator-facing explanation.
FAILURE_TAXONOMY = {
    "illegal memory access": (
        "OpenSplat issue #239 (Blackwell cull bug): rerun with "
        "--warmup-length > num-iters; quality cost: densification disabled."
    ),
    "clamp_max": (
        "OpenSplat issue #239 signature (clamp_max): rerun with "
        "--warmup-length > num-iters; quality cost: densification disabled."
    ),
    "bucketize": (
        "OpenSplat issue #239 signature (bucketize): rerun with "
        "--warmup-length > num-iters; quality cost: densification disabled."
    ),
    "no kernel image": (
        "Image built without sm_120 kernels — rebuild with "
        "CMAKE_CUDA_ARCHITECTURES=120. Do not retry with this image."
    ),
    "CUDA out of memory": (
        "VRAM exhausted (densification grows the model during training) — "
        "raise the downscale factor (-d) or lower num_iters."
    ),
    "Killed": (
        "RAM OOM during image import (issue #134 signature) — raise the "
        "downscale factor (-d)."
    ),
}


# ─── AVAILABILITY CHECK ──────────────────────────────────────────────────────

def check_opensplat() -> bool:
    """Return True if the pinned OpenSplat image exists in the WSL docker.

    Cheap enough for the GUI health-poll timer. A False here means either WSL
    is parked (start-nodeodm.ps1 boots it) or the image was never built /
    was pruned — see obsidian-dev/projects/sentinel-aerial/opensplat-status.md.
    """
    try:
        rc = subprocess.run(
            ["wsl", "-e", "docker", "image", "inspect", OPENSPLAT_IMAGE],
            capture_output=True, timeout=30,
        ).returncode
        return rc == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


# ─── PATH HELPERS ────────────────────────────────────────────────────────────

def to_wsl_path(win_path) -> str:
    """Convert a Windows path (E:\\foo\\bar) to its WSL mount (/mnt/e/foo/bar)."""
    p = PureWindowsPath(win_path)
    if not p.drive:
        raise ValueError(f"Expected an absolute Windows path, got: {win_path}")
    drive = p.drive[0].lower()
    rest = "/".join(p.parts[1:])
    return f"/mnt/{drive}/{rest}"


def pick_downscale_factor(photo_count, base=2):
    """Photo-count downscale heuristic (RAM is the scale limiter, not VRAM).

    OpenSplat preloads ALL images as float32 (~12 B/px + pyramid cache)
    into the 20 GB WSL VM. Measured/derived anchors: d=1 stalls 12 GB VRAM
    at ANY count (2026-07-22); 905 photos at 20.9 MP needs d≈5 to fit in
    RAM (plan D9). Returns max(base, heuristic) — never below the preset's
    floor, refined at the Phase 4 gate.
    """
    if photo_count <= 150:
        heuristic = 2
    elif photo_count <= 400:
        heuristic = 3
    elif photo_count <= 700:
        heuristic = 4
    else:
        heuristic = 5
    return max(base, heuristic)


# ─── PROJECT ASSEMBLY ────────────────────────────────────────────────────────

def extract_opensfm(all_zip, project_dir) -> Path:
    """Extract opensfm/reconstruction.json + image_list.txt from a NodeODM
    all.zip into <project_dir>/opensfm/. Returns the opensfm dir.

    Raises ValueError if either file is missing from the archive — NodeODM
    silently skips typo'd `outputs` paths, so task success alone proves
    nothing (verified caveat, 2026-07-22).
    """
    project_dir = Path(project_dir)
    opensfm_dir = project_dir / "opensfm"
    opensfm_dir.mkdir(parents=True, exist_ok=True)

    required = ["opensfm/reconstruction.json", "opensfm/image_list.txt"]
    with zipfile.ZipFile(all_zip) as z:
        names = set(z.namelist())
        missing = [r for r in required if r not in names]
        if missing:
            raise ValueError(
                f"NodeODM all.zip is missing {missing} — was the task "
                "submitted with the opensfm outputs override? "
                f"(archive contains: {sorted(names)[:8]}...)"
            )
        for r in required:
            with z.open(r) as src, open(opensfm_dir / Path(r).name, "wb") as dst:
                shutil.copyfileobj(src, dst)
    return opensfm_dir


def rewrite_image_list(opensfm_dir, container_images_dir=None) -> list:
    """Rewrite image_list.txt lines to absolute in-container paths.

    NodeODM writes absolute container-local paths
    (/var/www/data/<uuid>/images/<name>); OpenSplat resolves relative lines
    against the opensfm/ dir, so absolute /work paths sidestep depth
    ambiguity entirely. Returns the list of basenames for coverage checks.
    """
    if container_images_dir is None:
        container_images_dir = f"{CONTAINER_WORK}/odm_project/images"
    list_path = Path(opensfm_dir) / "image_list.txt"
    basenames = []
    lines = []
    for line in list_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        bn = line.rsplit("/", 1)[-1]
        basenames.append(bn)
        lines.append(f"{container_images_dir}/{bn}")
    # LF only — this file is consumed inside the Linux container, and
    # Windows text-mode CRLF turns every path into "...JPG\r" (file not
    # found at image-load time). Bug found live 2026-07-23.
    with open(list_path, "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return basenames


def preflight_project(project_dir) -> list:
    """Assert the assembled project is trainable. Returns list of problems
    (empty = pass).

    Checks (all verified failure modes, 2026-07-22):
    - every image in image_list.txt exists in images/ (OpenSplat's reader has
      no existence check; missing files only surface mid-training)
    - every shot key in reconstruction.json is covered by the image list
      (uncovered shots fail only at image-load time)
    """
    project_dir = Path(project_dir)
    problems = []
    list_path = project_dir / "opensfm" / "image_list.txt"
    recon_path = project_dir / "opensfm" / "reconstruction.json"
    images_dir = project_dir / "images"

    listed = []
    for line in list_path.read_text().splitlines():
        if not line.strip():
            continue
        bn = line.strip().rsplit("/", 1)[-1]
        listed.append(bn)
        if not (images_dir / bn).is_file():
            problems.append(f"listed image missing on disk: {bn}")

    listed_set = set(listed)
    recons = json.loads(recon_path.read_text())
    for recon in recons:
        for shot in recon.get("shots", {}):
            if shot not in listed_set:
                problems.append(f"shot not covered by image_list: {shot}")
    return problems


# ─── PIPELINE ────────────────────────────────────────────────────────────────

def run_opensplat_pipeline(
    photo_dir,
    working_dir,
    progress_callback=None,
    num_iters=30000,
    downscale_factor=2,
    all_zip=None,
):
    """Assemble an ODM project and train a Gaussian Splat in the pinned
    OpenSplat container.

    Args:
        photo_dir: Directory containing the ORIGINAL staged photos.
        working_dir: Scratch dir; odm_project/ is assembled inside it and
            splat.ply lands here. Also expected to contain all.zip from the
            NodeODM run unless all_zip is passed explicitly.
        progress_callback: Called with float 0-100.
        num_iters: Training iterations (30000 default).
        downscale_factor: OpenSplat -d. VRAM/RAM lever; 23 photos at d=1
            filled ~11.7 of 12 GB VRAM during densification (measured
            2026-07-22) — keep >=2 for real jobs. Automatically raised by
            pick_downscale_factor() for large photo counts (RAM ceiling).
        all_zip: Path to the NodeODM all.zip (default: working_dir/all.zip).

    Returns:
        {returncode, working_dir, gs_ply_dir, gs_sog_dir: None, error: str|None}
    """
    working_dir = Path(working_dir)
    photo_dir = Path(photo_dir)
    all_zip = Path(all_zip) if all_zip else working_dir / "all.zip"
    project_dir = working_dir / "odm_project"
    images_dir = project_dir / "images"

    result = {
        "returncode": 1,
        "working_dir": str(working_dir),
        "gs_ply_dir": None,
        "gs_sog_dir": None,
        "error": None,
    }

    # Assemble: opensfm files + staged photos
    try:
        opensfm_dir = extract_opensfm(all_zip, project_dir)
        basenames = rewrite_image_list(opensfm_dir)
        images_dir.mkdir(parents=True, exist_ok=True)
        for bn in basenames:
            src = photo_dir / bn
            dst = images_dir / bn
            if src.is_file() and not dst.is_file():
                shutil.copy2(src, dst)
        problems = preflight_project(project_dir)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        result["error"] = f"project assembly failed: {e}"
        log.error(result["error"])
        return result

    if problems:
        result["error"] = "preflight failed: " + "; ".join(problems[:5])
        log.error(result["error"])
        return result

    effective_d = pick_downscale_factor(len(basenames), base=downscale_factor)
    if effective_d != downscale_factor:
        log.info("Downscale raised %s -> %s for %d photos (RAM ceiling)",
                 downscale_factor, effective_d, len(basenames))
    downscale_factor = effective_d

    if progress_callback:
        progress_callback(0.0)
    log.info("OpenSplat loading images (no step output until training starts)")

    # Train
    cmd = [
        "wsl", "-e", "docker", "run", "--rm",
        "--name", CONTAINER_NAME, "--gpus", "all",
        "-v", f"{to_wsl_path(working_dir)}:{CONTAINER_WORK}",
        OPENSPLAT_IMAGE, OPENSPLAT_BIN,
        f"{CONTAINER_WORK}/odm_project",
        "-o", f"{CONTAINER_WORK}/splat.ply",
        "-n", str(num_iters),
        "-d", str(downscale_factor),
        "-s", "5000",
    ]
    log.info("Launching OpenSplat: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        errors="replace",
    )

    tail = []
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            tail.append(line)
            tail = tail[-40:]
        m = _STEP_RE.search(line)
        if m and progress_callback:
            step = int(m.group(1))
            progress_callback(min(100.0, 100.0 * step / num_iters))
    proc.wait()
    result["returncode"] = proc.returncode

    if proc.returncode != 0:
        blob = "\n".join(tail)
        for signature, explanation in FAILURE_TAXONOMY.items():
            if signature in blob:
                result["error"] = explanation
                break
        else:
            result["error"] = f"OpenSplat exited {proc.returncode}: {tail[-3:]}"
        log.error("OpenSplat failed: %s", result["error"])
        return result

    # Package output like the MipMap tree: working_dir/3D/model-gs-ply/
    gs_ply_dir = working_dir / "3D" / "model-gs-ply"
    gs_ply_dir.mkdir(parents=True, exist_ok=True)
    splat = working_dir / "splat.ply"
    if not splat.is_file():
        result["returncode"] = 1
        result["error"] = "OpenSplat exited 0 but wrote no splat.ply"
        log.error(result["error"])
        return result
    shutil.copy2(splat, gs_ply_dir / "splat.ply")
    cameras = working_dir / "cameras.json"
    if cameras.is_file():
        shutil.copy2(cameras, gs_ply_dir / "cameras.json")

    if progress_callback:
        progress_callback(100.0)
    result["gs_ply_dir"] = str(gs_ply_dir)
    return result


# ─── OUTPUT COPIER ────────────────────────────────────────────────────────────

def copy_splat_outputs(working_dir, dest_dir):
    """Copy splat outputs to destination. Same contract as
    mipmap_service.copy_splat_outputs: {dir_name: dest_path} of dirs copied,
    log-warn (not fail) on missing dirs. model-gs-sog-tile/ never exists for
    OpenSplat runs — downstream verified tolerant (2026-07-22).
    """
    working_dir = Path(working_dir)
    dest_dir = Path(dest_dir)
    copied = {}
    for dir_name in ["model-gs-ply", "model-gs-sog-tile"]:
        src = working_dir / "3D" / dir_name
        dst = dest_dir / dir_name
        if src.exists() and src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied[dir_name] = str(dst)
            log.info("Copied %s -> %s", src, dst)
        else:
            log.warning("Expected output not found: %s", src)
    return copied
