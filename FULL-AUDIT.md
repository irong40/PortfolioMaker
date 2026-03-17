# Full Audit Report
**Date**: 2026-03-16
**Branch**: main
**Auditor**: Codex (OpenAI gpt-5.4)
**Author**: Claude Code (Anthropic)
**Scope**: Full repo audit of all `.py` files plus `requirements.txt`

## Executive Summary
This repo is a small desktop application for classifying drone photos and wrapping the workflow in Tkinter. The core helpers are readable, but the operationally risky paths are the least defended: file-copy/move flows have no rollback or per-file recovery, the GUI has no automated coverage, and the architecture is already too monolithic for the planned redesign.

Security risk is moderate rather than severe; there is no obvious high-confidence remote exploit path. The larger problem is reliability and regression risk: the app mutates user photo sets, yet metadata parsing, sorting, exporting, and GUI behavior are mostly untested.

## Scorecard
| Dimension | Grade | Critical | Important | Minor |
|---|---|---:|---:|---:|
| Code Quality | D | 1 | 3 | 3 |
| Security | C | 0 | 1 | 1 |
| Test Coverage | D | 1 | 2 | 0 |
| Architecture | D | 0 | 3 | 0 |

## Code Quality

### CQ1: file operations are not failure-safe — Critical
- **Files**: `photo_classifier.py:284`, `photo_classifier.py:364`, `portfolio_maker.py:509`, `portfolio_maker.py:591`
- **Issue**: `sort_photos()` and `export_photos()` call `shutil.copy2` / `shutil.move` directly for each file with no per-file error handling, no rollback, and no collision strategy.
- **Impact**: a single I/O failure can leave the job in a partially copied or moved state. In move mode this is a real data-loss workflow hazard.
- **Fix**: add structured transfer error handling, success/failure accounting, collision checks, and a rollback or resumable journal.

### CQ2: fallback metadata extraction swallows all parsing failures — Important
- **File**: `photo_classifier.py:46`
- **Issue**: both fallback extractors catch `Exception` and return `None` silently.
- **Impact**: corrupt files, parser bugs, unsupported EXIF layouts, and dependency problems all look like "no metadata".
- **Fix**: catch narrower exceptions and log filename plus reason.

### CQ3: partially entered bbox values are silently ignored — Important
- **Files**: `portfolio_maker.py:330`, `portfolio_maker.py:557`
- **Issue**: `_get_bbox()` returns `None` as soon as any bbox field is blank. Export then treats that as "no bbox filter".
- **Impact**: users can think they set an area filter when the app actually exports the full dataset.
- **Fix**: require all four bbox fields or none, and raise a specific validation error for partial input.

### CQ4: manifest omits the `unknown` output folder — Minor
- **Files**: `photo_classifier.py:276`, `photo_classifier.py:406`
- **Issue**: `sort_photos()` creates an `unknown` folder, but the result object and manifest only record `nadir` and `oblique`.
- **Fix**: persist `unknown_dir` in both.

### CQ5: unused imports indicate weak cleanup discipline — Minor
- **Files**: `photo_classifier.py:25`, `generate_icon.py:8`, `test_photo_classifier.py:4`
- **Issue**: several imports are unused.
- **Fix**: lint and remove them.

### CQ6: `create_shortcut.py` executes side effects at import time — Minor
- **File**: `create_shortcut.py:18`
- **Issue**: shortcut creation happens at module import rather than under a `main()` guard.
- **Fix**: move the body into `main()`.

## Security

### S1: dynamic import from a hardcoded external checkout weakens trust boundaries — Important
- **File**: `photo_classifier.py:30`
- **OWASP**: A08 Software and Data Integrity Failures
- **Issue**: the app prepends `C:\Users\redle.SOULAAN\Documents\drone-pipeline` to `sys.path` and imports executable code from there if present.
- **Attack Vector**: anyone who can tamper with that directory can change what this app executes on startup.
- **Fix**: make this an explicit config, pin/package the dependency, or vendor the needed logic locally.

### S2: destination writes can overwrite existing files without warning — Minor
- **Files**: `photo_classifier.py:295`, `photo_classifier.py:367`
- **Issue**: exports and sorted outputs reuse original filenames with no collision check.
- **Impact**: local data integrity risk.
- **Fix**: fail on collision by default or rename deterministically.

## Test Coverage

### T1: the highest-risk workflows have no automated tests — Critical
- **Files**: `photo_classifier.py:186`, `photo_classifier.py:260`, `photo_classifier.py:346`, `portfolio_maker.py:105`
- **Issue**: tests only cover `classify_pitch`, `filter_photos`, `scan_photos`, and `write_manifest`.
- **Missing**: `classify_photos`, metadata extraction integration, `sort_photos`, `export_photos`, CLI behavior, GUI workflows, shortcut script, icon script.

### T2: failure paths are effectively untested — Important
- **File**: `test_photo_classifier.py`
- **Missing cases**: unreadable files, malformed metadata, copy/move failures, manifest write failures, duplicate filenames, partial bbox entry.

### T3: test environment is not reproducibly declared — Important
- **Files**: `requirements.txt:1`, `test_photo_classifier.py:9`, `create_shortcut.py:7`
- **Issue**: only Pillow is declared; `pytest`, `winshell`, and `pywin32` are not.
- **Fix**: add `requirements-dev.txt` or a `pyproject.toml` with runtime/dev extras.

## Architecture

### A1: `portfolio_maker.py` is already a god module — Important
- **File**: `portfolio_maker.py:105`
- **Issue**: one class owns styling, layout, validation, state, threading, workflow orchestration, and result rendering.
- **Impact**: the planned GUI redesign and NodeODM integration will increase coupling and make testing harder.
- **Fix**: split into view components, workflow/service layer, and controller.

### A2: repo reproducibility depends on undeclared external state — Important
- **Files**: `photo_classifier.py:30`, `requirements.txt:1`
- **Issue**: behavior changes depending on whether an external `drone-pipeline` checkout exists locally, but that dependency is not packaged or versioned here.

### A3: GUI and CLI duplicate orchestration instead of sharing a service boundary — Important
- **Files**: `photo_classifier.py:433`, `portfolio_maker.py:416`
- **Issue**: both entry points own workflow sequencing directly.
- **Fix**: introduce a reusable orchestration/service layer.

## Final Verdict
- [ ] SHIP IT
- [x] REWORK
- [ ] BLOCK
