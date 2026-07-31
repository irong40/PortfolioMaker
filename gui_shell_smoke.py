"""Drive the restyled Sortie shell without a human.

Proves the things the pytest suite cannot: that every original panel still
exists, is parented to a real step page, and that the four pack(before=...)
reveal chains still resolve now that panels live on separate pages.
"""
import sys, os
sys.path.insert(0, r"D:\Projects\PortfolioMaker")
os.chdir(r"D:\Projects\PortfolioMaker")

import tempfile, pathlib
import tkinter as tk
import sortie

# CRITICAL: this script calls _on_reset(), which does SETTINGS_FILE.unlink().
# Point that at a throwaway file BEFORE building the app, or running the smoke
# test destroys the operator's real sortie_settings.json (source folder, output
# folder, job type, site name, threshold, NodeODM URL, client profile, window
# geometry). The file is gitignored, so there is no recovery.
_TMP_SETTINGS = pathlib.Path(tempfile.mkdtemp(prefix="sortie-smoke-")) / "sortie_settings.json"
sortie.SETTINGS_FILE = _TMP_SETTINGS
assert sortie.SETTINGS_FILE != pathlib.Path(r"D:\Projects\PortfolioMaker") / "sortie_settings.json"

fail = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fail.append(msg)

root = tk.Tk()
app = sortie.PortfolioMakerApp(root)
root.update_idletasks()

print("\n[1] every step page exists and is reachable")
for key, label in sortie.STEPS:
    check(key in app._pages, f"page '{key}' built")
    app._show_step(key)
    root.update_idletasks()
    check(app._pages[key].winfo_ismapped(), f"page '{key}' shows when selected")
    others = [k for k, _ in sortie.STEPS if k != key]
    check(not any(app._pages[o].winfo_ismapped() for o in others),
          f"page '{key}' is the only one mapped")

print("\n[2] original panels survived, on the expected page")
expect = {
    "_crm_combo": "mission",
    "_ppk_frame": "preflight",
    "scan_btn": "preflight",
    "_results_frame": "process",
    "_video_panel": "process",
    "_adv_toggle_frame": "process",
    "_adv_frame": "process",
    "_action_frame": "process",
    "progress_bar": "process",
    "results_text": "process",
    "_deliver_btn": "deliver",
}
for attr, page_key in expect.items():
    w = getattr(app, attr, None)
    check(w is not None, f"{attr} exists")
    if w is None:
        continue
    # walk up to the step page
    node, found = w, None
    while node is not None:
        for k, p in app._pages.items():
            if node is p:
                found = k
        node = node.master
    check(found == page_key, f"{attr} lives on '{page_key}' (found: {found})")

print("\n[3] the four pack(before=...) reveal chains still resolve")
app._show_step("preflight")
try:
    app._ppk_frame.pack(fill="x", pady=(0, 8), before=app.scan_btn.master)
    root.update_idletasks()
    check(app._ppk_frame.winfo_ismapped(), "PPK banner reveals before scan button")
except tk.TclError as e:
    check(False, f"PPK banner reveal raised: {e}")

app._show_step("process")
try:
    app._results_frame.pack(fill="x", pady=(0, 8), before=app._adv_toggle_frame)
    app._action_frame.pack(fill="x", pady=(0, 8), before=app.progress_bar)
    root.update_idletasks()
    check(app._results_frame.winfo_ismapped(), "results reveal before advanced toggle")
    check(app._action_frame.winfo_ismapped(), "actions reveal before progress bar")
except tk.TclError as e:
    check(False, f"results/actions reveal raised: {e}")

try:
    app._show_video_panel([{"path": "x.mp4", "name": "x.mp4",
                            "has_srt": False, "srt_path": None}])
    root.update_idletasks()
    check(app._video_panel.winfo_ismapped(), "video panel reveals before advanced toggle")
    app._hide_video_panel()
except tk.TclError as e:
    check(False, f"video panel reveal raised: {e}")

try:
    app._toggle_advanced()
    root.update_idletasks()
    check(app._adv_frame.winfo_ismapped(), "advanced expands before action frame")
    app._toggle_advanced()
except tk.TclError as e:
    check(False, f"advanced toggle raised: {e}")

print("\n[4] deliver reveal + status strip")
app._show_step("deliver")
try:
    app._deliver_hint_var.set("Ready to deliver:  E:\\Portfolio\\Test")
    app._deliver_btn.pack(anchor="w", pady=(10, 0))
    root.update_idletasks()
    check(app._deliver_btn.winfo_ismapped(), "deliver button reveals on deliver step")
except tk.TclError as e:
    check(False, f"deliver reveal raised: {e}")

app._set_crm_status(sortie.GREEN, "CRM · 14 open")
app._set_mission_chip("SAI-SPEC-012", linked=True)
app._update_nodeodm_indicator({"version": "3.5.3"})
app._update_mipmap_indicator()
root.update_idletasks()
check(app._crm_status_label.cget("text") == "CRM · 14 open", "CRM chip repaints")
check(app._mission_chip_var.get() == "SAI-SPEC-012", "mission chip repaints")

print("\n[5] reset still clears everything")
app._show_step("deliver")
app._deliver_btn.pack(anchor="w", pady=(10, 0))
root.update_idletasks()
check(app._deliver_btn.winfo_ismapped(), "deliver button visible before reset")
try:
    app._on_reset()
    root.update_idletasks()
    check(not app._results_frame.winfo_ismapped(), "reset hides results")
    check(not app._action_frame.winfo_ismapped(), "reset hides actions")
    # Regression guard: deliver used to be inside _action_frame, so reset hid
    # it for free. It is on its own step now and must be hidden explicitly,
    # or reset leaves a live Drive upload aimed at the previous job.
    check(not app._deliver_btn.winfo_ismapped(),
          "reset hides deliver button (no stale Drive upload)")
    check("Sort for Client" in app._deliver_hint_var.get(),
          "reset clears the stale 'Ready to deliver' path")
except Exception as e:
    check(False, f"_on_reset raised: {e}")

print("\n[6] the app follows the work (cross-check HIGH finding)")
# Scan lives on Pre-Flight; progress bar and live log live on Process. Without
# _set_running auto-advancing, pressing Scan leaves the operator on a page that
# never changes for the whole job.
app._show_step("preflight")
root.update_idletasks()
app._set_running(True)
root.update_idletasks()
check(app._current_step == "process",
      "starting a job advances to the Process step")
check(app.progress_bar.winfo_ismapped(), "progress bar visible once running")
check(app.results_text.winfo_ismapped(), "live log visible once running")
app._set_running(False)

print("\n[7] the UI does not assert unverified state")
# fetch_open_missions() returns [] on ANY failure, so empty must not be green.
app._populate_crm_dropdown([])
root.update_idletasks()
check(app._crm_status_label.cget("fg") != sortie.GREEN,
      "empty CRM result is NOT painted green")
# A refresh that invalidates the selection must drop the header chip too.
app._set_mission_chip("SAI-SPEC-012", linked=True)
app.crm_mission_var.set("SAI-SPEC-999 — gone from the CRM")
app._populate_crm_dropdown([])
root.update_idletasks()
check(app._mission_chip_var.get() == "Practice / Portfolio",
      "invalidated selection clears the mission chip")
check("Sort for Client" in app._deliver_hint_var.get(),
      "deliver hint names the only path that actually unlocks it")

root.destroy()
print("\n" + ("=" * 52))
print(f"RESULT: {'PASS' if not fail else 'FAIL — ' + str(len(fail)) + ' issue(s)'}")
for f in fail:
    print("  -", f)
sys.exit(1 if fail else 0)
