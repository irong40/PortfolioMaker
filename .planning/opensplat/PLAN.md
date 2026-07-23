# OpenSplat Sortie Integration — FINAL Implementation Plan

**Date:** 2026-07-22
**Status:** APPROVED FOR EXECUTION (design tournament winner + grafts + claim-verification rework applied)
**Branch:** `feat/opensplat` (dev untouched; tag `working-2026-07-22` assumed present)
**Budget:** $0 cash target (all open-source, existing hardware, existing R2/Vercel)

---

## 1. Why

MipMap's `reconstruct_full_engine.exe` free tier hard-caps at 512 images (misreports as "No License") and the CLI requires a rotating per-GUI-session `--desktop_magic` token — no clean headless path. Missions scale to ~2000 photos. Replacement: self-built **OpenSplat** (open-source 3D Gaussian Splatting, pierotofy/OpenSplat) running as a per-job CUDA container in the existing WSL2 docker stack, fed camera poses by the already-running NodeODM.

## 2. Architecture (summary)

- **SfM:** NodeODM 2.2.4 (existing container, localhost:3000) runs the full ODM pipeline; the splat preset submits with a custom `outputs` list so `all.zip` contains ONLY `opensfm/reconstruction.json` + `opensfm/image_list.txt` (a few MB, not GB). The `outputs` field **replaces** the default archive whitelist — it is passed on the splat preset ONLY; mesh/ortho presets are untouched and never re-state the default list.
- **Training:** `docker run --rm --name opensplat --gpus all -v <workdir>:/work opensplat:<shortsha>-cu128 /work/odm_project -o /work/splat.ply -n <iters> -d <downscale>` — no restart policy, invoked per job, honors the one-GPU-app rule, zero changes to WSL lifecycle scripts except a ~5-line shutdown guard in `stop-nodeodm.ps1`.
- **Service layer:** new `opensplat_service.py` mirrors `mipmap_service.py`'s exact engine contract (`run_*_pipeline(photo_dir, working_dir, progress_callback, **settings)` → `{returncode, working_dir, gs_ply_dir, gs_sog_dir}` + copy step). Does NOT import mipmap_service (isolates its hard `sentinel_core.metadata` import).
- **Sortie changes:** preset `engine` key flip (`mipmap` → `opensplat`), one new branch in `portfolio_service.process_job`, a preflight gate + health dot in `sortie.py`, two small branches in `report_generator.py`. Rollback = revert the preset engine key.
- **Output tree:** `<output_dir>/model-gs-ply/splat.ply`. `model-gs-sog-tile/` intentionally absent — VERIFIED nothing downstream references either dir and copy semantics log-warn on missing dirs.
- **Display (Option B):** static `public/splat-viewer/index.html` in sentinel-landing (mkkellogg GaussianSplats3D via CDN) + a NEW `scripts/ingest-splat.mjs`; `ingest-3d.mjs` and the Cesium mesh path untouched.
- **Explicitly cut (post-ship candidates):** SOG/compressed tiles, Cesium 1.130 / 3D-Tiles-splat, georeferenced splats (`--keep-crs`), `--end-with opensfm` SfM-only speedup (unverified against NodeODM task-completion semantics — first post-ship optimization, NOT in v1).

## 3. Dependency Ledger (load-bearing facts, labeled)

| # | Dependency | Label | Evidence / gate |
|---|---|---|---|
| D1 | NodeODM `/task/new` `outputs` field replaces default all.zip whitelist | **VERIFIED 2026-07-22** (source-level, Task.js:461-462 in the deployed 2.2.4 container) | Caveats: missing/typo'd paths are **silently skipped** (task still succeeds); malformed JSON silently degrades to the full default zip. Gate must assert both files are IN the zip. End-to-end curl not yet run → Phase 2 task. |
| D2 | Default all.zip does NOT contain `opensfm/` — the outputs override is mandatory, not an optimization | **VERIFIED 2026-07-22** (Task.js:444-459 allPaths list) | Original claim "fetch promptly and it's in the zip" was REFUTED; plan always passes `outputs` explicitly. |
| D3 | OpenSplat consumes an ODM project: falls through to `opensfm/reconstruction.json`; needs only reconstruction.json + image_list.txt + ORIGINAL images; brown/perspective models; distortion handled internally (cv::undistort); relative image_list lines resolve against the `opensfm/` dir | **VERIFIED 2026-07-22** (input_data.cpp / opensfm.cpp, main branch) | Relative-path DEPTH is layout-dependent (`../../photos/` resolves to a *sibling* of the project root; use `../photos/` if photos sit inside the root) → Phase 2 spot-check. |
| D4 | Docker build with `CUDA_VERSION=12.8.x` build-arg | **REFUTED as originally designed → REWORKED** | `.github/workflows/cuda/Linux.sh` hard-codes cu102–cu124 and `exit 1`s on cu128. Phase 1 patches this (see below). libtorch 2.7.1+cu128 zip existence on download.pytorch.org: VERIFIED (HTTP 200). Patched build: **ASSUMED until Phase 1 gate passes**; native Windows build (VS2022 + CUDA 12.8+ + libtorch cu128/cu129 + arch 120, issue #239 reporter's config) is the empirically-proven fallback. |
| D5 | nvidia-container-toolkit is the ONLY missing GPU piece in WSL (native docker.io 29.1.3, systemd PID 1, driver libs already in /usr/lib/wsl/lib, RTX 5070 12227 MiB visible) | **VERIFIED 2026-07-22** (live inspection) | Not in Ubuntu repos — needs NVIDIA apt repo + key + `nvidia-ctk runtime configure` + docker restart (~4 commands, one component). |
| D6 | Engine plug-point contract in `portfolio_service.process_job` (lines 229-259) + pct-only progress callback + generic (stage, detail) GUI polling | **VERIFIED 2026-07-22** (source) | Engine callback is pct-only float; `notify("processing", f"OpenSplat {pct}%")` wrapping happens in process_job. Preflight gate at sortie.py:~1670 and health dot (1164-1166, 1249-1256) need an opensplat variant. |
| D7 | `model-gs-ply/` output compat; nothing breaks on missing `model-gs-sog-tile/` | **VERIFIED 2026-07-22** (repo-wide grep of sentinel-landing = zero refs; copy_splat_outputs log-warns and continues; downstream uses .get()) | test_mipmap_service.py fabricates both dirs — add a companion test for the single-dir case. |
| D8 | Issue #239: deterministic CUDA illegal-memory-access in clamp_max/bucketize after a small-cull refinement step ~11-12k iters on sm_120; only workaround `--warmup-length > --num-iters` (disables densification, costs quality) | **VERIFIED OPEN 2026-07-22** (single reporter, RTX 5090, libtorch 2.8+cu129, Windows) | Trigger is a small-cull EVENT not a step number; sm_120-specificity is hypothesis (cull path has pre-Blackwell history, closed #206). A clean FoodLion 30k pass under OUR build = "not reproduced under our config", provisional — keep the workaround documented regardless. |
| D9 | Memory model: ~2000 B VRAM/gaussian (README); upfront CPU-RAM preload of ALL images as float32 (~12 B/pixel) + pyramid cache (~×1.33) | VRAM rule **VERIFIED** (README + source); RAM arithmetic **DERIVED/ASSUMED** | Quailshire = 905 × 20.9 MP: d=4 ≈ 18.8 GB (marginal vs 20 GB WSL cap), d=5 ≈ 12 GB (fits). d=5 is the safe default; empirical verification IS the Phase 4 gate. |
| D10 | OpenSplat CLI levers: `-n`, `-d` (clamped ≥1), `-o`, `--save-every`/`--resume`, stdout `Step <n>: <loss> (<pct>%)` every 10 steps; NO --max-splats cap | **VERIFIED 2026-07-22** (opensplat.cpp cxxopts table, main) | No output during pre-training image load (progress sits at 0); `--resume` restores splats+step but NOT optimizer state (coarse checkpoint). |
| D11 | NodeODM task trees persist ~48h (`cleanupTasksAfter: 2880`); no Sortie preset sets `--optimize-disk-space` | **VERIFIED 2026-07-22** | Container has ZERO volume mounts — task trees die on container recreation. We rely on the tiny all.zip fetched immediately at completion, so this is defense-in-depth only. |
| D12 | `opensfm/` exists at project root post-run (not scattered under `submodels/`) | **ASSUMED — must verify** | Non-splat presets use `split: 200` split-merge, which scatters opensfm under `submodels/` on >200-photo sets. The gaussian_splat preset MUST NOT carry split/split-overlap options. Phase 2 (23 photos, safe) + explicit Phase 3 preset check + Phase 4 (905 photos, the real test). |
| D13 | GPU passthrough WSL → container | Host-level **VERIFIED tonight** (nvidia-smi in WSL); container-level **BUILT-UNTESTED** until Phase 1 smoke test | `nvidia-smi` in container passes regardless of CUDA arch — the sm_120 kernel test is a separate Phase 1/2 check. |

## 4. Phases

### Phase 1 — GPU container enablement (WSL only, zero Sortie code)

**Tasks:**
1. Add NVIDIA's apt repo + GPG key in WSL Ubuntu 26.04; `apt install nvidia-container-toolkit`; `sudo nvidia-ctk runtime configure --runtime=docker`; `sudo systemctl restart docker`. (~4 commands — toolkit is NOT in Ubuntu repos.)
2. **Post-restart check (graft):** `docker ps` — confirm the NodeODM container and all 5 Firecrawl containers come back healthy. Protects two unrelated revenue systems from a silent regression.
3. GPU-in-docker smoke test: `docker run --rm --gpus all nvidia/cuda:12.8.1-base-ubuntu22.04 nvidia-smi` must show the RTX 5070.
4. Clone pierotofy/OpenSplat into WSL native ext4 (NOT /mnt). **Record and pin the main-branch commit SHA** (graft — kills rebuild drift).
5. **REWORK (D4 refuted):** the stock Dockerfile fails on any CUDA 12.8.x because `.github/workflows/cuda/Linux.sh` only knows cu102–cu124. Fix on the pinned clone, preferred order:
   - (a) Swap the base image to `nvidia/cuda:12.8.1-devel-ubuntu22.04` and delete the `RUN bash .github/workflows/cuda/Linux.sh ...` layer (toolkit already in base), keeping the parameterized libtorch fetch (2.7.1+cu128 zip VERIFIED present); or
   - (b) Extend Linux.sh's case statement with a `cu128` entry (cuda-repo-ubuntu2204-12-8-local deb from developer.download.nvidia.com).
6. Build: `docker build --build-arg UBUNTU_VERSION=22.04 --build-arg CUDA_VERSION=12.8.1 --build-arg TORCH_VERSION=2.7.1 --build-arg CMAKE_CUDA_ARCHITECTURES=120 -t opensplat:<shortsha>-cu128 .` — **never `:latest`, never default args** (defaults 12.1.1/2.2.1/70;75;80 are unusable on Blackwell).
7. Fallback if the patched build still fails (libtorch ABI/API drift): native Windows build per issue #239's proven recipe (VS2022 + CUDA 12.8/13.1 + libtorch 2.7/2.8 cu128/cu129 + `-DCMAKE_CUDA_ARCHITECTURES=120`). The service layer shells out to a command — runner is swappable.
8. Start `obsidian-dev/projects/sentinel-aerial/opensplat-status.md` ledger (graft): pinned SHA, image tag, build args, and VERIFIED/BUILT-UNTESTED per component; update at every gate.

**Verification gate (no dataset — toolchain gate):** `docker run --rm --gpus all opensplat:<shortsha>-cu128 --help` prints the cxxopts table, AND a CUDA tensor op inside the image does NOT throw "no kernel image is available" (the sm_120 failure signature). NodeODM + Firecrawl confirmed healthy post-restart.

**Rollback:** `docker image rm` the opensplat image; `apt remove nvidia-container-toolkit` + delete the nvidia runtime stanza from /etc/docker/daemon.json + restart docker. No Windows-side or Sortie-side state touched.

**Est:** 3–5 h active.

### Phase 2 — FoodLion 23-photo proof run (MANDATORY, zero Sortie code)

Dataset: `D:\50c59097-5dd8-4ef2-8f78-e62544e90dad\FoodLion\FoodLion-20260722` (23 photos).

**Tasks:**
1. Submit the 23 photos to NodeODM via raw curl with form field `outputs='["opensfm/reconstruction.json","opensfm/image_list.txt"]'` and NO split/split-overlap options. This exercises D1 end-to-end (the one part of the outputs mechanism not yet run) before any nodeodm.py change.
2. Download all.zip and **assert both opensfm files are present inside it** — D1's silent-skip caveat means task success alone proves nothing. Also confirms D12 for the ≤200-photo case (opensfm at project root).
3. Inspect image_list.txt: record the exact container-local path format NodeODM wrote, and pin the correct relative rewrite depth (`../../photos/` if photos are a sibling of the project root, `../photos/` if inside it — D3 caveat).
4. Hand-assemble the project dir (opensfm/ + staged original photos, same filenames), rewrite image_list.txt lines, and verify **every listed image exists on disk** (graft — OpenSplat's reader has no existence check; missing files only surface mid-training).
5. Fast smoke: `docker run --rm --name opensplat --gpus all -v ...:/work opensplat:<shortsha>-cu128 /work/odm_project -n 2000 -o /work/splat.ply`.
6. Full run: `-n 30000` with default densification — deliberately crossing the ~11-12k small-cull window to characterize issue #239 on the 5070 (graft: 30k, not 15k, for margin). If it crashes with "illegal memory access"/clamp_max/bucketize: rerun once with `--warmup-length 40000` and record the quality delta as the standing workaround. **A clean pass is provisional** (our libtorch/CUDA build differs from the reporter's; trigger is a cull event, not a step) — the workaround stays documented either way.
7. Visually verify splat.ply in SuperSplat or antimatter15/splat (drag-and-drop, zero site work). Record wall-clock, peak VRAM (nvidia-smi), peak WSL RAM (free/htop).

**Verification gate (FoodLion):** a visually sane splat.ply produced end-to-end from NodeODM SfM + containerized OpenSplat on the 5070, with #239 behavior characterized (clean pass OR documented workaround), and the image_list path format pinned. **NO Sortie wiring starts until this passes.** Update the vault ledger.

**Rollback:** nothing to roll back — no code changed; delete scratch dirs.

**Est:** 3–5 h active + training wall-clock.

### Phase 3 — Sortie wiring on feat/opensplat

**Tasks:**
1. `git checkout -b feat/opensplat` in D:\Projects\PortfolioMaker (dev stays working; MipMap code untouched everywhere).
2. `sentinel_core/nodeodm.py`: add optional `outputs` kwarg to `submit_task` (serialized JSON array posted to /task/new). Plumbed **splat-preset-only** — never passed for mesh/ortho presets, never re-stating the default deliverable list (a missed path silently strips client deliverables; if a mission needs ortho + splat, run two tasks).
3. New `opensplat_service.py` mirroring mipmap_service.py (NO import of mipmap_service):
   - `check_opensplat()` → bool: `wsl docker image inspect opensplat:<shortsha>-cu128` (pinned tag), cached, polled on the existing health timer.
   - `run_opensplat_pipeline(photo_dir, working_dir, progress_callback, num_iters, downscale_factor)` → `{returncode, working_dir, gs_ply_dir, gs_sog_dir: None}`: extract the two opensfm files into `working_dir/odm_project/opensfm/`; rewrite image_list.txt to the Phase-2-pinned relative form; **preflight-assert every listed image exists AND that every shot basename in reconstruction.json is covered by the list** (D3 caveat: uncovered shots fail only at image-load time); Popen the container with `--name opensplat`; parse stdout `Step N/M` → `progress_callback(pct)` (emit an explicit "loading images" detail before step output begins, since stdout is silent during preload).
   - **Failure taxonomy (graft — matcher only, NO auto-retry, per the auth-error-protocol spirit):** stderr contains "illegal memory access"/clamp_max/bucketize → "OpenSplat issue #239 (Blackwell cull bug): rerun with --warmup-length > num-iters; quality cost: densification disabled"; "no kernel image is available" → "image built without sm_120 — rebuild with CMAKE_CUDA_ARCHITECTURES=120, do not retry"; process "Killed" during image import → "RAM OOM (issue #134 signature) — raise -d".
   - `copy_outputs()` → `<output_dir>/model-gs-ply/splat.ply` (+ cameras.json if produced), `{dir_name: dest_path}` shape; add a unit test for the SOG-dir-absent case alongside the existing mipmap test.
4. `odm_presets.py`: gaussian_splat preset `engine: "mipmap"` → `"opensplat"`; replace `mipmap_settings` with `opensplat_settings {num_iters: 30000, downscale_factor: 2}` (revisited at Phase 4); keep min_photos/photo_filter. **Verify (D12):** the preset's ODM options contain NO split/split-overlap. **Verify:** the line-315 platform-override skip covers the new engine string. Never set `optimize-disk-space` on this preset.
5. `portfolio_service.py process_job`: `engine == "opensplat"` branch mirroring the mipmap branch (229-259): stage filtered photos → `submit_task(..., outputs=[the two opensfm paths])` → poll → download the few-MB all.zip **immediately at completion** and assert both files present → `run_opensplat_pipeline` → `notify("processing", f"OpenSplat {pct:.0f}%")` → copy outputs; report_data carries opensplat_settings.
6. `sortie.py`: preflight `elif engine == "opensplat" and not (self._opensplat_ok and self._nodeodm_ok)` (splat needs BOTH); wire check_opensplat into the health-poll timer + header dot (1164-1166 / 1249-1256 pattern), relabeled per engine.
7. `report_generator.py`: `opensplat` branches in `_render_methodology` ("trained with OpenSplat, an open-source 3D Gaussian Splatting implementation...") and `_render_processing_details` (iterations, downscale factor).
8. `stop-nodeodm.ps1` guard (graft — the one cut Design 1 shouldn't have made): before `wsl --shutdown`, run `docker ps --filter name=opensplat` inside WSL and refuse with a message unless `-Force`. ~5 lines protecting multi-hour GPU runs; the existing queue check only sees NodeODM.

**Verification gate (FoodLion, via GUI):** end-to-end run driven entirely from Sortie: progress percentages visible in the UI, output at `E:\Portfolio\FoodLion\<date>\gaussian_splat\model-gs-ply\splat.ply`, gaussian_splat report generates without error. **Then the rollback check (graft):** flip the preset engine key back to `"mipmap"` and confirm the old path still runs, flip forward again. Only after both: merge feat/opensplat → dev.

**Rollback:** pre-merge: `git checkout dev` (untouched). Post-merge: revert the merge commit, or operationally flip the preset `engine` key back to `"mipmap"` (one line — MipMap code is still in the repo).

**Est:** 8–12 h active.

### Phase 4 — Quailshire 905-photo scale gate

Dataset: `E:\Portfolio\Quailshire\_mipmap_work\photos` (905 photos, 5280×3956 / 20.9 MP verified).

**Tasks:**
1. Run Quailshire through the new Sortie path. NodeODM SfM on CPU takes hours — schedule it; do NOT run `stop-nodeodm.ps1` mid-job (guard from Phase 3 now refuses, but plan around it anyway).
2. Confirm (D12, the real test): with the splat preset's no-split options, opensfm/ lands at the project root — not `submodels/` — at 905 photos, and both files appear in all.zip.
3. Start OpenSplat at **`-d 5` (safe default per D9 arithmetic: ≈12 GB vs 20 GB cap); attempt `-d 4` (≈18.8 GB, marginal) only as a stretch** while watching WSL RAM (free/htop) and VRAM (nvidia-smi). OOM-killer "Killed" during import = issue #134 signature → step d up. (Deliberately NOT building a RAM-formula auto-downscaler — the constants are derived; the empirical result here becomes a simple photo-count heuristic instead.)
4. Use `--save-every` so a crash/shutdown is resumable via `--resume` (coarse: optimizer state not restored).
5. Write the empirical defaults into odm_presets.py (heuristic: ≤100 photos d=1–2, ~1000 d=4–5) and document the ~2000-photo ceiling + required d in the vault project folder + opensplat-status.md.

**Verification gate (Quailshire):** completes without OOM (RAM and 12 GB VRAM inside limits), usable splat.ply, defaults committed. This is the go/no-go for calling the pipeline production-ready toward ~2000-photo missions.

**Rollback:** config-only — revert preset defaults; no structural change.

**Est:** 3–5 h active + many hours wall-clock (CPU SfM + training).

### Phase 5 — Portfolio display (Option B, minimal site change)

**Tasks:**
1. sentinel-landing (dev branch; `git fetch && git log HEAD..origin/dev` first per standing rule): add static `public/splat-viewer/index.html` using mkkellogg GaussianSplats3D from jsdelivr (same CDN pattern as the Cesium page), reading `?url=<ply>&title=&sub=`.
2. New `scripts/ingest-splat.mjs` (graft — sibling script, `ingest-3d.mjs` untouched): upload `model-gs-ply/splat.ply` to R2 under `<slug>/`, append a portfolio-3d.json entry with `viewer: "splat"` discriminator + splatUrl.
3. `portfolioData.ts`: map `viewer: "splat"` entries to `iframe3d.src = /splat-viewer/?url=...` (drop-in sibling of the /3d-viewer/ mapping). **Verify R2 CORS allows the .ply fetch** (open question — check bucket CORS config before assuming).
4. Ingest the FoodLion splat; verify on a Vercel preview deployment before merging dev → main.

**Verification gate (FoodLion, in browser):** FoodLion splat renders from the Vercel preview URL on desktop; existing Cesium mesh portfolio entries still load unchanged. Out of scope (later): .splat/.spz compression for bandwidth.

**Rollback:** revert the sentinel-landing commits on dev; nothing merged to main until the gate passes; Cesium path never touched.

**Est:** 4–6 h active.

## 5. Risks

1. **Issue #239 (Blackwell cull crash)** — deterministic illegal memory access on sm_120 after small-cull events (~11-12k iters); only workaround disables densification and costs quality. Phase 2 deliberately crosses the window; a pass is provisional (different libtorch/CUDA than the reporter; event-triggered, not step-triggered). Workaround stays in the failure taxonomy.
2. **Docker build combo unproven** — the stock Dockerfile is REFUTED for CUDA 12.8 (Linux.sh case statement); the patched build is a small, understood fix but has no published success report. Mitigation: pinned SHA, and the native Windows build IS empirically proven on sm_120; the service layer treats the runner as a swappable shell command.
3. **RAM, not VRAM, is the scale limiter** — upfront float32 preload of all images (~12 B/px + pyramid). Numbers are DERIVED; d=5 is the safe Quailshire start; ~2000-photo missions may force d=5+ or a native Windows run with the full 32 GB. The Quailshire gate exists precisely to replace arithmetic with measurement.
4. **`outputs` replaces (not appends)** — passing it on a non-splat preset silently strips ortho/DSM deliverables; missing paths are silently dropped from the zip. Contained: splat-preset-only plumbing, two-file list, in-zip assertion at download, Phase 3 rollback check.
5. **Split-merge scatter (D12)** — `split: 200` would bury opensfm under `submodels/` at Quailshire scale. Contained by keeping split options out of the splat preset; verified at both Phase 2 (small) and Phase 4 (905).
6. **`wsl --shutdown` killing a training run** — mitigated by the stop-nodeodm.ps1 guard (Phase 3) + `--save-every`/`--resume`.
7. **NodeODM task trees are ephemeral** — 48h cleanup AND zero volume mounts (die on container recreation). Contained by fetching the tiny zip immediately at task completion.
8. **image_list.txt rewrite is the fragile joint** — no existence check in the reader; basename-keyed map fails late. Contained by the Phase 2 format spot-check + the preflight existence/coverage assert.
9. **Full ODM run wasted on SfM-only needs** — hours of CPU on discarded ortho/mesh stages for big sets. `--end-with opensfm` is the flagged first post-ship optimization, deliberately out of v1.
10. **Non-georeferenced output** — OpenSplat recenters/rescales; fine for the standalone viewer, but Cesium geo-placement would need `--keep-crs` + reference_lla — out of scope.
11. **One-GPU-app rule** — splat training cannot coexist with other GPU work (SDXL/ComfyUI) in 12 GB; schedule like NodeODM jobs.

## 6. Open Questions (every UNCERTAIN item → a named verification task)

| Q | Uncertainty | Resolved by |
|---|---|---|
| Q1 | `outputs` mechanism confirmed at source but never exercised end-to-end on this instance | Phase 2 task 1-2 (curl + in-zip assert) |
| Q2 | Does ODM 3.5.6 leave `opensfm/reconstruction.json` + `image_list.txt` on disk at completion under the splat preset's options? | Phase 2 task 2 |
| Q3 | Exact path format NodeODM writes into image_list.txt, and correct relative rewrite depth (`../../photos/` vs `../photos/`) | Phase 2 task 3 |
| Q4 | Does #239 reproduce on the 5070 under our cu128/libtorch-2.7.1 build? (pass = provisional) | Phase 2 task 6 |
| Q5 | Real RAM footprint at 905 photos; d=4 viable or d=5 floor; ~2000-photo ceiling | Phase 4 gate |
| Q6 | Splat preset truly carries no split/split-overlap, and opensfm stays at project root at 905 photos | Phase 3 task 4 + Phase 4 task 2 |
| Q7 | Patched Docker build (cu128 base-image swap or Linux.sh patch) actually completes and produces sm_120 kernels | Phase 1 gate |
| Q8 | Line-315 platform-override skip in odm_presets.py covers `engine == "opensplat"` | Phase 3 task 4 |
| Q9 | R2 bucket CORS permits cross-origin .ply fetch from the Vercel domain | Phase 5 task 3 |
| Q10 | `--resume` behavior acceptable after a mid-training kill (optimizer state lost) | Phase 4 task 4 (observe quality after any resume) |

## 7. Cost Estimate

| Phase | Active hours | Wall-clock extra | Cash |
|---|---|---|---|
| 1 — GPU container | 3–5 | docker build ~1h | $0 |
| 2 — FoodLion proof | 3–5 | 30k-iter training | $0 |
| 3 — Sortie wiring | 8–12 | — | $0 |
| 4 — Quailshire gate | 3–5 | CPU SfM hours + training | $0 |
| 5 — Portfolio display | 4–6 | — | $0 |
| **Total** | **21–33 h** | spread over ~1–2 wks | **$0** (OSS + existing hardware/R2/Vercel) |

## 8. Rollback Summary

- **Global:** all Sortie work on `feat/opensplat`; dev untouched until the Phase 3 gate (including the mipmap-flip-back check) passes; tag `working-2026-07-22` is the hard restore point. MipMap code is never deleted — operational rollback is the one-line preset `engine` key revert.
- **Per phase:** P1 = remove image + toolkit config; P2 = nothing (no code); P3 = branch discard or merge revert; P4 = config revert; P5 = sentinel-landing dev revert, main never touched pre-gate.
- **Ledger:** `obsidian-dev/projects/sentinel-aerial/opensplat-status.md` records SHA, tag, build args, and per-component VERIFIED-date / BUILT-UNTESTED / ASSUMED at every gate — no trusting stale validations.
