"""
Sortie — Desktop Application v2.0

Intent-driven GUI: pick a job type, scan photos, process via NodeODM
or sort locally. Produces client-ready deliverable packages.

Usage:
    python sortie.py
    (or double-click the desktop shortcut)
"""

import os
import sys
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# Ensure our own modules are importable
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import json

from photo_classifier import (
    classify_photos, PIPELINE_AVAILABLE,
    list_profiles, load_profile, classify_with_profile, sort_with_profile,
    compass_to_bearing,
)
from odm_presets import JOB_TYPES, get_preset, engine_requires_nodeodm
from portfolio_service import (
    check_nodeodm, scan_for_job, process_job, portfolio_only, PORTFOLIO_ROOT,
)
from mipmap_service import check_mipmap
from opensplat_service import check_opensplat
from ppk_service import (detect_rinex, run_ppk_correction,
                         MIN_RECOMMENDED_OBS_MINUTES)
from drive_delivery import (
    DriveUnavailableError, is_authenticated, authenticate,
    deliver as drive_deliver,
)
from mission_planner import MissionPlannerDialog
from video_stills import extract_cardinal_stills, has_yaw_telemetry
import crm_sync
from property_highlights import render_highlights, find_matching_kml

# ─── SETTINGS PERSISTENCE ────────────────────────────────────────────────────

SETTINGS_FILE = SCRIPT_DIR / "sortie_settings.json"

def load_settings():
    """Load saved settings from disk. Returns dict with defaults for missing keys."""
    defaults = {
        "source_dir": "",
        "output_dir": "",
        "job_type": "construction_progress",
        "site_name": "",
        "threshold": "-70",
        "nodeodm_url": "http://localhost:3000",
        "window_geometry": "",
    }
    try:
        with open(SETTINGS_FILE, "r") as f:
            saved = json.load(f)
        defaults.update(saved)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return defaults

def save_settings(settings):
    """Save settings dict to disk."""
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

CRM_MANUAL_CHOICE = "Manual (no CRM link)"


def scan_summary(classification, working_set, preset):
    """One line describing what the selected job type will actually process.

    Local (panorama) jobs consume the PANORAMA/ sets, which scan_photos prunes
    out of the working set — reporting working_set.total for those claimed 0
    photos while the run happily processed 26.
    """
    label = preset["label"]
    if preset.get("engine") == "local":
        sets = classification.panorama_sets if classification else []
        photos = sum(ps.photo_count for ps in sets)
        return (f"Using: {photos} photos in {len(sets)} panorama set(s) "
                f"({label} preset)")

    total = working_set.total if working_set else 0
    photo_filter = preset["photo_filter"]
    if photo_filter:
        return f"Using: {total} {photo_filter} photos ({label} preset)"
    return f"Using: {total} photos — all ({label} preset)"
# ─── COLORS / STYLE ────────────────────────────────────────────────────────

SENTINEL_PURPLE = "#5B2C6F"
SENTINEL_DARK = "#1A0A2E"
SENTINEL_LIGHT = "#F4ECF7"
SENTINEL_MID = "#AF7AC5"
ACCENT_GOLD = "#F4D03F"
BG_COLOR = "#F7F5F9"
CARD_BG = "#FFFFFF"
TEXT_DIM = "#7D6B8A"
GREEN = "#27AE60"
RED = "#E74C3C"
FONT_FAMILY = "Segoe UI"
ICON_FILE = SCRIPT_DIR / "sortie.ico"

# Shell chrome (stepper rail + status strip)
RAIL_BG = "#241040"          # rail sits between header and canvas in tone
RAIL_ACTIVE = "#3A1A5C"      # selected step pill
RAIL_TEXT = "#C9B3DA"
RAIL_TEXT_ACTIVE = "#FFFFFF"
RAIL_NUM_IDLE = "#6E5385"
HAIRLINE = "#E4DCEA"         # 1px separators, card borders
AMBER = "#D68910"            # "present but not a live probe"
CARD_TITLE = "#4A2560"

# Stepper definition — order is the real workflow order.
STEPS = [
    ("mission", "Mission"),
    ("source", "Source"),
    ("config", "Config"),
    ("preflight", "Pre-Flight"),
    ("process", "Process"),
    ("deliver", "Deliver"),
]

STEP_HINTS = {
    "mission": "Link a CRM mission, or work unlinked for practice and portfolio.",
    "source": "Where the photos are, and where the deliverables should land.",
    "config": "Job type, site name, and an optional client sort profile.",
    "preflight": "Parcel boundary, canopy calibration, and PPK correction.",
    "process": "Scan the folder, review the counts, then run the job.",
    "deliver": "Push finished deliverables to Drive.",
}


# ─── CUSTOM STYLES ─────────────────────────────────────────────────────────

def configure_styles():
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(".", font=(FONT_FAMILY, 9), background=BG_COLOR)
    style.configure("TFrame", background=BG_COLOR)
    style.configure("TLabel", background=BG_COLOR, font=(FONT_FAMILY, 9))

    # Cards: flat, hairline-bordered, generous padding — replaces the
    # ridged 3-D LabelFrame look that made 13 stacked panels read as noise.
    style.configure("TLabelframe", background=BG_COLOR,
                    bordercolor=HAIRLINE, relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=BG_COLOR,
                    font=(FONT_FAMILY, 9, "bold"), foreground=CARD_TITLE)

    style.configure("Accent.TButton", font=(FONT_FAMILY, 10, "bold"),
                    padding=(18, 9), borderwidth=0, focuscolor=SENTINEL_PURPLE)
    style.map("Accent.TButton",
              background=[("disabled", "#C9BCD4"), ("active", SENTINEL_MID),
                          ("!active", SENTINEL_PURPLE)],
              foreground=[("disabled", "#F0EAF4"), ("active", "white"),
                          ("!active", "white")])

    style.configure("Secondary.TButton", font=(FONT_FAMILY, 9), padding=(13, 7))

    # Quiet tertiary button for rail nav ("Back")
    style.configure("Ghost.TButton", font=(FONT_FAMILY, 9), padding=(13, 7),
                    borderwidth=0)
    style.map("Ghost.TButton",
              background=[("active", "#EBE3F0"), ("!active", BG_COLOR)],
              foreground=[("!active", SENTINEL_PURPLE)])

    style.configure("Sentinel.Horizontal.TProgressbar",
                    troughcolor="#E8E0ED", background=SENTINEL_PURPLE,
                    thickness=8, borderwidth=0)

    style.configure("TEntry", padding=6)
    style.configure("TCombobox", padding=5)
    style.configure("TCheckbutton", background=BG_COLOR, font=(FONT_FAMILY, 9))
    style.configure("TRadiobutton", background=BG_COLOR, font=(FONT_FAMILY, 9))

    # Page heading inside a step
    style.configure("StepTitle.TLabel", font=(FONT_FAMILY, 15, "bold"),
                    foreground=SENTINEL_DARK, background=BG_COLOR)
    style.configure("StepHint.TLabel", font=(FONT_FAMILY, 9),
                    foreground=TEXT_DIM, background=BG_COLOR)


# ─── STAT BADGE WIDGET ────────────────────────────────────────────────────

class StatBadge(tk.Frame):
    def __init__(self, parent, label, value="—", color=SENTINEL_PURPLE, **kwargs):
        super().__init__(parent, bg=CARD_BG, padx=12, pady=8, **kwargs)
        self._value_var = tk.StringVar(value=str(value))
        tk.Label(self, textvariable=self._value_var,
                 font=(FONT_FAMILY, 20, "bold"), fg=color, bg=CARD_BG).pack()
        tk.Label(self, text=label,
                 font=(FONT_FAMILY, 8), fg=TEXT_DIM, bg=CARD_BG).pack()

    def set(self, value):
        self._value_var.set(str(value))


# ─── MAIN APPLICATION ──────────────────────────────────────────────────────

class PortfolioMakerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sortie")
        self.root.configure(bg=BG_COLOR)
        self.root.resizable(True, True)
        # Wider (rail + page) but much shorter — steps replaced the tall scroll.
        self.root.minsize(940, 660)

        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except tk.TclError:
                pass

        self._classification = None
        self._working_set = None
        self._last_sort_output = None  # Path to last sort output for delivery
        self._found_videos = []
        self._running = False
        self._nodeodm_ok = False
        self._mipmap_ok = False
        self._ppk_rinex = None
        self._ppk_corrected = False
        self._cancel_event = None
        self._crm_missions = []
        self._crm_job = None
        self._settings = load_settings()

        configure_styles()
        self._build_menubar()
        self._build_header()
        self._build_status_bar()  # Pack bottom first so canvas doesn't steal its space
        self._build_shell()

        # Each builder packs into self._content; point it at the step page
        # that owns those panels. Panels linked by pack(before=...) must stay
        # on one page — preflight+PPK+scan, and results+video+advanced+actions.
        self._content = self._pages["mission"]
        self._build_input_section()   # walks mission → source → config

        self._content = self._pages["preflight"]
        self._build_ppk_banner()
        self._build_preflight_section()
        self._build_scan_button()

        self._content = self._pages["process"]
        self._build_results_section()
        self._build_video_panel()
        self._build_advanced_section()
        self._build_action_buttons()
        self._build_progress_section()

        self._content = self._pages["deliver"]
        self._build_deliver_section()

        # Restore saved settings
        self._apply_settings()
        self._center_window()

        # Hide results, actions, and PPK banner until needed
        self._results_frame.pack_forget()
        self._video_panel.pack_forget()
        self._action_frame.pack_forget()
        self._ppk_frame.pack_forget()

        # Open on the first step
        self._show_step(STEPS[0][0])

        # Save settings on close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Check NodeODM in background
        threading.Thread(target=self._check_nodeodm_bg, daemon=True).start()

        # Load CRM missions in background (fails soft to manual mode)
        threading.Thread(target=self._load_crm_missions_bg, daemon=True).start()

        # Check for PPK data if a source folder is already set
        saved_source = self._settings.get("source_dir", "")
        if saved_source and os.path.isdir(saved_source):
            self._check_ppk_data(saved_source)

    def _center_window(self):
        saved_geo = self._settings.get("window_geometry", "")
        if saved_geo:
            self.root.geometry(saved_geo)
        else:
            self.root.update_idletasks()
            x = (self.root.winfo_screenwidth() // 2) - 500
            y = (self.root.winfo_screenheight() // 2) - 360
            self.root.geometry(f"1000x720+{x}+{y}")

    def _apply_settings(self):
        """Restore saved settings into UI vars."""
        s = self._settings
        if s.get("source_dir"):
            self.source_var.set(s["source_dir"])
        if s.get("output_dir"):
            self.output_var.set(s["output_dir"])
        if s.get("job_type"):
            self.job_type_var.set(s["job_type"])
        if s.get("site_name"):
            self.site_name_var.set(s["site_name"])
        if s.get("threshold"):
            self.threshold_var.set(s["threshold"])
        if s.get("nodeodm_url"):
            self.nodeodm_url_var.set(s["nodeodm_url"])
        if s.get("client_profile") and hasattr(self, 'profile_var'):
            self.profile_var.set(s["client_profile"])

    def _gather_settings(self):
        """Collect current UI state into a settings dict."""
        return {
            "source_dir": self.source_var.get(),
            "output_dir": self.output_var.get(),
            "job_type": self.job_type_var.get(),
            "site_name": self.site_name_var.get(),
            "threshold": self.threshold_var.get(),
            "nodeodm_url": self.nodeodm_url_var.get(),
            "client_profile": self.profile_var.get() if hasattr(self, 'profile_var') else "",
            "window_geometry": self.root.geometry(),
        }

    def _on_close(self):
        """Save settings and close the application."""
        try:
            save_settings(self._gather_settings())
        except OSError:
            pass
        self.root.destroy()

    # ── Build: Menu Bar ──

    def _build_menubar(self):
        menubar = tk.Menu(self.root)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Generate Bees360 Mission...",
                               command=self._open_mission_planner)
        tools_menu.add_command(label="Pull Flight Logs from RC",
                               command=self._pull_flight_logs)
        tools_menu.add_separator()
        tools_menu.add_command(label="Property Highlights...",
                               command=self._open_property_highlights)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        self.root.config(menu=menubar)

    def _open_mission_planner(self):
        MissionPlannerDialog(self.root, self._settings, save_settings_cb=save_settings)

    def _pull_flight_logs(self):
        """Subprocess drone-pipeline's pull_flight_logs.py; show status in a messagebox."""
        import subprocess, json as _json, threading
        from pathlib import Path as _Path

        pipeline_dir = _Path(self._settings.get(
            "drone_pipeline_path",
            r"C:\Users\redle.SOULAAN\Documents\drone-pipeline",
        ))
        script_path = pipeline_dir / "pull_flight_logs.py"
        if not script_path.exists():
            messagebox.showerror(
                "pull_flight_logs.py not found",
                f"Could not find:\n  {script_path}\n\nUpdate drone-pipeline.",
                parent=self.root,
            )
            return

        # Non-blocking; show a "working..." toplevel while subprocess runs
        working = tk.Toplevel(self.root)
        working.title("Pulling Flight Logs")
        working.transient(self.root)
        working.grab_set()
        working.resizable(False, False)
        tk.Label(working, text="Reading flight records from RC...\nThis takes a few seconds per log.",
                 font=(FONT_FAMILY, 10), padx=24, pady=20, bg=BG_COLOR).pack()
        working.update_idletasks()
        working.geometry(f"+{self.root.winfo_rootx() + 100}+{self.root.winfo_rooty() + 100}")

        def worker():
            argv = [sys.executable, str(script_path)]
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True,
                    cwd=str(pipeline_dir), timeout=600,
                )
            except subprocess.TimeoutExpired:
                self.root.after(0, self._pull_logs_done, working,
                                {"status": "error", "error": "Timed out after 10 min."})
                return
            except Exception as e:
                self.root.after(0, self._pull_logs_done, working,
                                {"status": "error", "error": str(e)})
                return

            # The script prints its JSON result on the last non-empty stdout line.
            payload = None
            for line in reversed(proc.stdout.strip().splitlines()):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        payload = _json.loads(line)
                        break
                    except _json.JSONDecodeError:
                        continue
            if payload is None:
                payload = {"status": "error",
                           "error": proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"}
            self.root.after(0, self._pull_logs_done, working, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _pull_logs_done(self, working_window, payload):
        try:
            working_window.grab_release()
            working_window.destroy()
        except tk.TclError:
            pass

        if payload.get("status") != "ok":
            messagebox.showerror("Pull Flight Logs", payload.get("error", "Unknown error"),
                                 parent=self.root)
            return

        copied = payload.get("copied", 0)
        already = payload.get("already_local", 0)
        failed = payload.get("failed", 0)
        size_kb = payload.get("bytes_copied", 0) / 1024
        dest = payload.get("dest", "")

        lines = [
            f"Pulled {copied} new log{'s' if copied != 1 else ''} ({size_kb:.1f} KB)",
            f"{already} already on disk",
        ]
        if failed:
            lines.append(f"{failed} failed")
        lines.append("")
        lines.append(f"Filed under: {dest}")
        lines.append("Drag the contents of the month folder to airdata.com/upload.")

        messagebox.showinfo("Flight Logs Pulled", "\n".join(lines), parent=self.root)

    # ── Build: Header ──

    def _build_header(self):
        header = tk.Frame(self.root, bg=SENTINEL_PURPLE, padx=20, pady=12)
        header.pack(fill="x")

        title_row = tk.Frame(header, bg=SENTINEL_PURPLE)
        title_row.pack(fill="x")

        tk.Label(title_row, text="Sortie",
                 font=(FONT_FAMILY, 17, "bold"), fg="white",
                 bg=SENTINEL_PURPLE).pack(side="left")

        # Mission chip — the one line that says which job you are on.
        self._mission_chip_var = tk.StringVar(value="Practice / Portfolio")
        self._mission_chip = tk.Label(
            title_row, textvariable=self._mission_chip_var,
            font=(FONT_FAMILY, 9, "bold"), fg="#E8DAF0", bg=RAIL_ACTIVE,
            padx=10, pady=3)
        self._mission_chip.pack(side="left", padx=(12, 0))

        engine = "drone-pipeline" if PIPELINE_AVAILABLE else "standalone"
        tk.Label(title_row, text=f"engine: {engine}",
                 font=(FONT_FAMILY, 8), fg=SENTINEL_MID,
                 bg=SENTINEL_PURPLE).pack(side="right")

        # ── Connection strip ──────────────────────────────────────────────
        # Honest by construction: live-probed services get a real dot that
        # _update_*_indicator recolors. n8n is NOT probed (its processing
        # tier was retired 2026-07-27, ADR: sortie is the system of record),
        # so it renders as a muted informational chip, never a green dot.
        strip = tk.Frame(self.root, bg=RAIL_BG, padx=20, pady=7)
        strip.pack(fill="x")

        # CRM (live — populated by _populate_crm_dropdown)
        self._crm_status_frame = tk.Frame(strip, bg=RAIL_BG)
        self._crm_status_frame.pack(side="left", padx=(0, 20))
        self._crm_dot = tk.Canvas(self._crm_status_frame, width=10, height=10,
                                  bg=RAIL_BG, highlightthickness=0)
        self._crm_dot.pack(side="left", padx=(0, 5))
        self._crm_dot.create_oval(1, 1, 9, 9, fill=TEXT_DIM, outline="")
        self._crm_status_label = tk.Label(
            self._crm_status_frame, text="CRM checking…",
            font=(FONT_FAMILY, 8), fg=RAIL_TEXT, bg=RAIL_BG)
        self._crm_status_label.pack(side="left")

        # NodeODM (live probe — attribute names preserved for the updaters)
        self._nodeodm_frame = tk.Frame(strip, bg=RAIL_BG)
        self._nodeodm_frame.pack(side="left", padx=(0, 20))
        self._nodeodm_dot = tk.Canvas(self._nodeodm_frame, width=10, height=10,
                                       bg=RAIL_BG, highlightthickness=0)
        self._nodeodm_dot.pack(side="left", padx=(0, 5))
        self._nodeodm_dot.create_oval(1, 1, 9, 9, fill=TEXT_DIM, outline="")
        self._nodeodm_label = tk.Label(self._nodeodm_frame, text="NodeODM",
                                        font=(FONT_FAMILY, 8), fg=RAIL_TEXT,
                                        bg=RAIL_BG)
        self._nodeodm_label.pack(side="left")

        # Splat engine (live probe)
        self._mipmap_frame = tk.Frame(strip, bg=RAIL_BG)
        self._mipmap_frame.pack(side="left", padx=(0, 20))
        self._mipmap_dot = tk.Canvas(self._mipmap_frame, width=10, height=10,
                                      bg=RAIL_BG, highlightthickness=0)
        self._mipmap_dot.pack(side="left", padx=(0, 5))
        self._mipmap_dot.create_oval(1, 1, 9, 9, fill=TEXT_DIM, outline="")
        self._mipmap_label = tk.Label(self._mipmap_frame, text="Splat engine",
                                       font=(FONT_FAMILY, 8), fg=RAIL_TEXT,
                                       bg=RAIL_BG)
        self._mipmap_label.pack(side="left")

        # n8n — informational only, deliberately not a status dot
        n8n_frame = tk.Frame(strip, bg=RAIL_BG)
        n8n_frame.pack(side="right")
        tk.Label(n8n_frame, text="n8n: alerts only · sortie processes locally",
                 font=(FONT_FAMILY, 8), fg=RAIL_NUM_IDLE,
                 bg=RAIL_BG).pack(side="left")

    # ── Build: Shell (stepper rail + step pages + nav) ──

    def _build_shell(self):
        """Left rail of numbered steps, a scrollable page area, and a nav bar.

        Replaces the single 13-panel scrolling column. Every original panel
        still exists and keeps its widgets, variables and callbacks — this
        only changes which parent it is packed into and when it is shown.
        """
        body = ttk.Frame(self.root)
        body.pack(fill="both", expand=True)

        # ── Rail ──
        rail = tk.Frame(body, bg=RAIL_BG, width=158)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)

        self._rail_rows = {}
        for idx, (key, label) in enumerate(STEPS):
            row = tk.Frame(rail, bg=RAIL_BG, padx=12, pady=9)
            row.pack(fill="x")
            num = tk.Label(row, text=str(idx + 1),
                           font=(FONT_FAMILY, 9, "bold"),
                           fg=RAIL_NUM_IDLE, bg=RAIL_BG, width=2)
            num.pack(side="left")
            name = tk.Label(row, text=label, font=(FONT_FAMILY, 9),
                            fg=RAIL_TEXT, bg=RAIL_BG, anchor="w")
            name.pack(side="left", fill="x", expand=True)
            self._rail_rows[key] = (row, num, name)

            # Rail entries are navigable — no gating, so nothing that worked
            # before becomes unreachable.
            for w in (row, num, name):
                w.bind("<Button-1>", lambda _e, k=key: self._show_step(k))
                w.configure(cursor="hand2")

        # ── Page area (scrollable, so tall steps still work) ──
        page_host = ttk.Frame(body)
        page_host.pack(side="left", fill="both", expand=True)

        self._scroll_canvas = tk.Canvas(page_host, bg=BG_COLOR,
                                         highlightthickness=0, bd=0)
        self._scroll_vsb = ttk.Scrollbar(page_host, orient="vertical",
                                          command=self._scroll_canvas.yview)
        self._scroll_canvas.configure(yscrollcommand=self._scroll_vsb.set)
        self._scroll_vsb.pack(side="right", fill="y")
        self._scroll_canvas.pack(side="left", fill="both", expand=True)

        self._scroll_inner = ttk.Frame(self._scroll_canvas)
        self._content_window = self._scroll_canvas.create_window(
            (0, 0), window=self._scroll_inner, anchor="nw")

        def _on_canvas_configure(event):
            self._scroll_canvas.itemconfigure(self._content_window,
                                              width=event.width)
        self._scroll_canvas.bind("<Configure>", _on_canvas_configure)

        def _on_content_configure(_event):
            self._scroll_canvas.configure(
                scrollregion=self._scroll_canvas.bbox("all"))
        self._scroll_inner.bind("<Configure>", _on_content_configure)

        def _on_mousewheel(event):
            self._scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)),
                                             "units")
        self._scroll_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        self._scroll_inner.configure(padding=(20, 16, 20, 0))

        # One frame per step. Builders target these via self._content.
        self._pages = {}
        for key, label in STEPS:
            page = ttk.Frame(self._scroll_inner)
            head = ttk.Frame(page)
            head.pack(fill="x", pady=(0, 12))
            ttk.Label(head, text=label, style="StepTitle.TLabel").pack(anchor="w")
            ttk.Label(head, text=STEP_HINTS.get(key, ""),
                      style="StepHint.TLabel").pack(anchor="w", pady=(2, 0))
            ttk.Separator(page, orient="horizontal").pack(fill="x", pady=(0, 12))
            self._pages[key] = page

        # ── Nav bar ──
        nav = tk.Frame(self.root, bg=BG_COLOR, padx=20, pady=10)
        nav.pack(fill="x", side="bottom")
        tk.Frame(nav, bg=HAIRLINE, height=1).pack(fill="x", side="top",
                                                  pady=(0, 10))
        self._back_btn = ttk.Button(nav, text="←  Back",
                                    command=self._step_back,
                                    style="Ghost.TButton")
        self._back_btn.pack(side="left")
        self._next_btn = ttk.Button(nav, text="Next  →",
                                    command=self._step_next,
                                    style="Secondary.TButton")
        self._next_btn.pack(side="right")

        self._current_step = STEPS[0][0]

    def _show_step(self, key):
        """Swap the visible step page and repaint the rail."""
        for page in self._pages.values():
            page.pack_forget()
        self._pages[key].pack(fill="both", expand=True)
        self._current_step = key

        for k, (row, num, name) in self._rail_rows.items():
            active = (k == key)
            bg = RAIL_ACTIVE if active else RAIL_BG
            row.configure(bg=bg)
            num.configure(bg=bg,
                          fg=RAIL_TEXT_ACTIVE if active else RAIL_NUM_IDLE)
            name.configure(bg=bg,
                           fg=RAIL_TEXT_ACTIVE if active else RAIL_TEXT,
                           font=(FONT_FAMILY, 9, "bold") if active
                           else (FONT_FAMILY, 9))

        idx = [k for k, _ in STEPS].index(key)
        self._back_btn.configure(state="normal" if idx > 0 else "disabled")
        self._next_btn.configure(
            state="normal" if idx < len(STEPS) - 1 else "disabled")
        self._scroll_canvas.yview_moveto(0)

    def _step_next(self):
        keys = [k for k, _ in STEPS]
        idx = keys.index(self._current_step)
        if idx < len(keys) - 1:
            self._show_step(keys[idx + 1])

    def _step_back(self):
        keys = [k for k, _ in STEPS]
        idx = keys.index(self._current_step)
        if idx > 0:
            self._show_step(keys[idx - 1])

    # ── Build: Input Section (folder + job type + site name) ──

    def _build_input_section(self):
        # CRM mission link (optional — sortie stays fully manual without it)
        crm_frame = ttk.LabelFrame(self._content, text="CRM Mission (optional)", padding=10)
        crm_frame.pack(fill="x", pady=(0, 8))

        crm_row = ttk.Frame(crm_frame)
        crm_row.pack(fill="x")
        ttk.Label(crm_row, text="Mission:").pack(side="left")
        self.crm_mission_var = tk.StringVar(value=CRM_MANUAL_CHOICE)
        self._crm_combo = ttk.Combobox(
            crm_row, textvariable=self.crm_mission_var,
            values=[CRM_MANUAL_CHOICE], state="readonly")
        self._crm_combo.pack(side="left", padx=(8, 0), fill="x", expand=True)
        self._crm_combo.bind("<<ComboboxSelected>>", self._on_crm_mission_selected)
        ttk.Button(crm_row, text="Refresh", command=self._on_crm_refresh,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))

        self._crm_hint_var = tk.StringVar(value="Checking CRM…")
        ttk.Label(crm_frame, textvariable=self._crm_hint_var,
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM,
                  wraplength=680, justify="left").pack(anchor="w", pady=(4, 0))

        # ── step: source ──
        self._content = self._pages["source"]

        # Photo folder
        src_frame = ttk.LabelFrame(self._content, text="Photo Folder", padding=10)
        src_frame.pack(fill="x", pady=(0, 8))

        row = ttk.Frame(src_frame)
        row.pack(fill="x")
        self.source_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.source_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse_source,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))

        # Output folder (optional override — defaults to E:\Portfolio\...)
        out_frame = ttk.LabelFrame(self._content, text="Output Folder (optional)", padding=10)
        out_frame.pack(fill="x", pady=(0, 8))

        out_row = ttk.Frame(out_frame)
        out_row.pack(fill="x")
        self.output_var = tk.StringVar()
        ttk.Entry(out_row, textvariable=self.output_var).pack(side="left", fill="x", expand=True)
        ttk.Button(out_row, text="Browse...", command=self._browse_output,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))
        ttk.Label(out_frame, text=f"Leave blank to use default: {PORTFOLIO_ROOT}\\<site>\\<date>\\<job>",
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(anchor="w", pady=(4, 0))

        # ── step: config ──
        self._content = self._pages["config"]

        # Job type + site name side by side
        job_frame = ttk.LabelFrame(self._content, text="Job Configuration", padding=10)
        job_frame.pack(fill="x", pady=(0, 8))

        # Job type radio buttons (2 columns)
        type_label = ttk.Label(job_frame, text="Job Type:")
        type_label.pack(anchor="w")

        radio_frame = ttk.Frame(job_frame)
        radio_frame.pack(fill="x", pady=(4, 8))

        self.job_type_var = tk.StringVar(value=JOB_TYPES[0][0])
        for i, (key, label) in enumerate(JOB_TYPES):
            col = i % 2
            row_num = i // 2
            rb = ttk.Radiobutton(radio_frame, text=label, variable=self.job_type_var, value=key)
            rb.grid(row=row_num, column=col, sticky="w", padx=(0, 40), pady=1)

        # Site name
        name_row = ttk.Frame(job_frame)
        name_row.pack(fill="x", pady=(4, 0))
        ttk.Label(name_row, text="Site Name:").pack(side="left")
        self.site_name_var = tk.StringVar()
        ttk.Entry(name_row, textvariable=self.site_name_var, width=30).pack(
            side="left", padx=(8, 0), fill="x", expand=True)

        # Description label that updates with job type
        self._job_desc_var = tk.StringVar()
        ttk.Label(job_frame, textvariable=self._job_desc_var,
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(anchor="w", pady=(4, 0))
        self.job_type_var.trace_add("write", self._update_job_desc)
        self._update_job_desc()

        # ── Client Profile ──
        profile_frame = ttk.LabelFrame(self._content, text="Client Profile (optional)", padding=10)
        profile_frame.pack(fill="x", pady=(0, 8))

        profile_row = ttk.Frame(profile_frame)
        profile_row.pack(fill="x")

        ttk.Label(profile_row, text="Client:").pack(side="left")
        self._profile_list = list_profiles()
        profile_choices = ["None (standard sort)"] + [p[1] for p in self._profile_list]
        self.profile_var = tk.StringVar(value="None (standard sort)")
        self._profile_combo = ttk.Combobox(
            profile_row, textvariable=self.profile_var,
            values=profile_choices, state="readonly", width=30)
        self._profile_combo.pack(side="left", padx=(8, 0))

        self._profile_desc_var = tk.StringVar(value="Standard nadir/oblique sort for SAI processing")
        ttk.Label(profile_frame, textvariable=self._profile_desc_var,
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(anchor="w", pady=(4, 0))

        # Front bearing (shown when profile requires it)
        self._bearing_frame = ttk.Frame(profile_frame)
        ttk.Label(self._bearing_frame, text="Front of structure faces:").pack(side="left")
        self.bearing_var = tk.StringVar(value="N")
        bearing_choices = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        self._bearing_combo = ttk.Combobox(
            self._bearing_frame, textvariable=self.bearing_var,
            values=bearing_choices, state="readonly", width=5)
        self._bearing_combo.pack(side="left", padx=(8, 0))
        ttk.Label(self._bearing_frame,
                  text="(which direction does the front door face?)",
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(side="left", padx=(8, 0))
        # Hidden until a directional profile is selected
        self.bearing_var.trace_add("write", self._on_bearing_change)

        self._profile_validation_var = tk.StringVar()
        self._profile_validation_label = ttk.Label(
            profile_frame, textvariable=self._profile_validation_var,
            font=(FONT_FAMILY, 9, "bold"), foreground=RED)
        self._profile_validation_label.pack(anchor="w", pady=(2, 0))
        self._profile_validation_label.pack_forget()  # hidden until scan

        self.profile_var.trace_add("write", self._on_profile_change)
        self._profile_result = None

    # ── Build: PPK Banner ──

    def _build_ppk_banner(self):
        self._ppk_frame = tk.Frame(self._content, bg="#FFF3CD", padx=12, pady=10,
                                    highlightbackground="#FFEEBA", highlightthickness=1)
        self._ppk_frame.pack(fill="x", pady=(0, 8))

        top_row = tk.Frame(self._ppk_frame, bg="#FFF3CD")
        top_row.pack(fill="x")

        tk.Label(top_row, text="PPK Data Detected",
                 font=(FONT_FAMILY, 10, "bold"), fg="#856404",
                 bg="#FFF3CD").pack(side="left")

        self._ppk_status_label = tk.Label(top_row, text="",
                                           font=(FONT_FAMILY, 8), fg="#856404",
                                           bg="#FFF3CD")
        self._ppk_status_label.pack(side="right")

        self._ppk_detail_var = tk.StringVar()
        tk.Label(self._ppk_frame, textvariable=self._ppk_detail_var,
                 font=(FONT_FAMILY, 9), fg="#856404", bg="#FFF3CD",
                 wraplength=700, justify="left").pack(anchor="w", pady=(4, 6))

        btn_row = tk.Frame(self._ppk_frame, bg="#FFF3CD")
        btn_row.pack(fill="x")

        self._ppk_correct_btn = ttk.Button(
            btn_row, text="Run PPK Correction",
            command=self._on_ppk_correct, style="Accent.TButton")
        self._ppk_correct_btn.pack(side="left")

        self._ppk_skip_btn = ttk.Button(
            btn_row, text="Skip (use raw GPS)",
            command=self._on_ppk_skip, style="Secondary.TButton")
        self._ppk_skip_btn.pack(side="left", padx=(8, 0))

        self._ppk_progress_var = tk.StringVar()
        tk.Label(self._ppk_frame, textvariable=self._ppk_progress_var,
                 font=(FONT_FAMILY, 8), fg="#856404", bg="#FFF3CD").pack(
            anchor="w", pady=(4, 0))

    def _show_ppk_banner(self, rinex):
        """Show the PPK banner with detected file info."""
        self._ppk_rinex = rinex
        self._ppk_corrected = False
        detail = f"RINEX files found alongside your photos."
        if rinex.obs_file:
            detail += f"\n  OBS: {Path(rinex.obs_file).name}"
        if rinex.mrk_file:
            detail += f"\n  MRK: {Path(rinex.mrk_file).name}"
        if rinex.nav_file:
            detail += f"\n  NAV: {Path(rinex.nav_file).name}"
        if rinex.approx_lat:
            detail += f"\n  Position: {rinex.approx_lat:.4f}, {rinex.approx_lon:.4f}"
        if rinex.flight_duration_minutes is not None:
            detail += f"\n  Capture: {rinex.flight_duration_minutes:.1f} min"
        detail += "\n\nRun PPK correction to get cm-accurate photo coordinates before processing."
        if (rinex.flight_duration_minutes is not None
                and rinex.flight_duration_minutes < MIN_RECOMMENDED_OBS_MINUTES):
            detail += (
                f"\n⚠ Capture is under {MIN_RECOMMENDED_OBS_MINUTES} min — "
                "PPK is unlikely to converge; photos keep raw GPS if it doesn't."
            )
        self._ppk_detail_var.set(detail)
        self._ppk_status_label.configure(text="Ready")
        self._ppk_progress_var.set("")
        self._ppk_correct_btn.configure(state="normal")
        self._ppk_frame.pack(fill="x", pady=(0, 8),
                              before=self.scan_btn.master)

    def _hide_ppk_banner(self):
        self._ppk_frame.pack_forget()

    def _on_ppk_correct(self):
        """Run PPK correction in a background thread."""
        if not self._ppk_rinex:
            return

        self._set_running(True)
        self._ppk_correct_btn.configure(state="disabled")
        self._ppk_skip_btn.configure(state="disabled")
        self._ppk_status_label.configure(text="Processing...")

        rinex = self._ppk_rinex
        msg_queue = queue.Queue()

        def run():
            try:
                def progress_cb(stage, detail):
                    msg_queue.put(("stage", stage, detail))

                result = run_ppk_correction(rinex, progress_callback=progress_cb)
                msg_queue.put(("done", result))
            except Exception as e:
                traceback.print_exc()
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(result):
            self._set_running(False)
            self._ppk_skip_btn.configure(state="normal")
            if result.success:
                self._ppk_corrected = True
                self._ppk_frame.configure(bg="#D4EDDA", highlightbackground="#C3E6CB")
                for widget in self._ppk_frame.winfo_children():
                    try:
                        widget.configure(bg="#D4EDDA")
                        for child in widget.winfo_children():
                            try:
                                child.configure(bg="#D4EDDA")
                            except tk.TclError:
                                pass
                    except tk.TclError:
                        pass
                self._ppk_status_label.configure(
                    text=f"Corrected: {result.photos_corrected}/{result.photos_total} "
                         f"({result.fix_rate:.0%} fix rate)",
                    fg="#155724")
                self._ppk_detail_var.set(
                    f"PPK correction complete using CORS station {result.cors_station} "
                    f"({result.baseline_km} km baseline).\n"
                    f"Photos are now cm-accurate. Proceed with Scan.")
                self._ppk_correct_btn.configure(state="disabled")
            else:
                self._ppk_status_label.configure(text="Failed", fg="#721C24")
                self._ppk_progress_var.set(f"Error: {result.error}")
                self._ppk_correct_btn.configure(state="normal")

        self._start_polling(msg_queue, on_done)

    def _on_ppk_skip(self):
        """Skip PPK correction and proceed with raw GPS."""
        self._ppk_corrected = False
        self._hide_ppk_banner()

    # ── Build: Pre-Flight (Parcel Lookup + ACL Calibration) ──

    def _build_preflight_section(self):
        pf_frame = ttk.LabelFrame(self._content, text="Parcel & Altitude", padding=10)
        pf_frame.pack(fill="x", pady=(0, 8))

        # Parcel lookup
        ttk.Label(pf_frame, text="Parcel Boundary (KML for WaypointMap)",
                  font=(FONT_FAMILY, 9, "bold")).pack(anchor="w")

        parcel_row = ttk.Frame(pf_frame)
        parcel_row.pack(fill="x", pady=(4, 0))
        ttk.Label(parcel_row, text="Address:").pack(side="left")
        self.parcel_address_var = tk.StringVar()
        ttk.Entry(parcel_row, textvariable=self.parcel_address_var).pack(
            side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(parcel_row, text="Lookup KML",
                   command=self._lookup_parcel,
                   style="Secondary.TButton").pack(side="left")

        self.parcel_result_var = tk.StringVar(value="")
        ttk.Label(pf_frame, textvariable=self.parcel_result_var,
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(anchor="w", pady=(2, 0))

        # Separator
        ttk.Separator(pf_frame, orient="horizontal").pack(fill="x", pady=8)

        # ACL Calibration
        ttk.Label(pf_frame, text="ACL Calibration (Above Canopy Level)",
                  font=(FONT_FAMILY, 9, "bold")).pack(anchor="w")

        acl_row1 = ttk.Frame(pf_frame)
        acl_row1.pack(fill="x", pady=(4, 0))
        ttk.Label(acl_row1, text="Canopy height (ft):").pack(side="left")
        self.canopy_height_var = tk.StringVar(value="0")
        ttk.Entry(acl_row1, textvariable=self.canopy_height_var, width=8).pack(side="left", padx=6)
        ttk.Label(acl_row1, text="Required ACL (ft):").pack(side="left", padx=(12, 0))
        self.required_acl_var = tk.StringVar(value="200")
        ttk.Entry(acl_row1, textvariable=self.required_acl_var, width=8).pack(side="left", padx=6)
        ttk.Button(acl_row1, text="Calc", command=self._calc_acl,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))
        ttk.Button(acl_row1, text="From Photo...", command=self._read_canopy_from_photo,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))

        self.rec_alt_var = tk.StringVar(value="")
        ttk.Label(pf_frame, textvariable=self.rec_alt_var,
                  font=(FONT_FAMILY, 10, "bold"), foreground=SENTINEL_PURPLE).pack(anchor="w", pady=(4, 0))

    def _lookup_parcel(self):
        """Look up parcel boundary and export KML."""
        address = self.parcel_address_var.get().strip()
        if not address:
            messagebox.showwarning("Missing Address", "Enter a property address first.")
            return

        self.parcel_result_var.set("Looking up parcel...")
        self.root.update()

        try:
            # Import from drone-pipeline
            sys.path.insert(0, r"C:\Users\redle.SOULAAN\Documents\drone-pipeline")
            from parcel_lookup import run as parcel_run
            result = parcel_run(address=address)

            self.parcel_result_var.set(
                f"{result['parcel_id']} | {result['acres']} acres | "
                f"Owner: {result.get('owner', 'N/A')} | KML saved"
            )

            kml_path = result["kml_path"]
            msg = (
                f"Parcel ID: {result['parcel_id']}\n"
                f"Area: {result['acres']} acres\n"
                f"Owner: {result.get('owner', 'N/A')}\n\n"
                f"KML saved to:\n{kml_path}\n\n"
                f"Load this into WaypointMap to define your survey area."
            )

            if messagebox.askyesno("Parcel Found", msg + "\n\nOpen KML file location?"):
                os.startfile(str(Path(kml_path).parent))

        except Exception as e:
            self.parcel_result_var.set(f"Error: {e}")
            messagebox.showerror("Parcel Lookup Failed", str(e))

    def _calc_acl(self):
        """Calculate recommended flight altitude from canopy height + required ACL."""
        try:
            canopy = float(self.canopy_height_var.get() or 0)
            acl = float(self.required_acl_var.get() or 200)
            rec = canopy + acl
            self.rec_alt_var.set(f"Recommended altitude: {rec:.0f} ft AGL")
        except ValueError:
            self.rec_alt_var.set("Invalid input")

    def _read_canopy_from_photo(self):
        """Read canopy height from a thermal photo's ObjectDistance EXIF field."""
        filepath = filedialog.askopenfilename(
            title="Select thermal calibration photo",
            filetypes=[
                ("Thermal photos", "*.jpg *.jpeg *.rjpeg *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not filepath:
            return

        try:
            from sentinel_core.metadata import extract_thermal_metadata
            thermal = extract_thermal_metadata(filepath)

            if not thermal:
                messagebox.showwarning("No Data", "No thermal metadata found in this photo.")
                return

            if "canopy_height" in thermal:
                height_m = thermal["canopy_height"]
                height_ft = height_m * 3.28084
                self.canopy_height_var.set(f"{height_ft:.0f}")
                self._calc_acl()
                messagebox.showinfo(
                    "ACL Calibration",
                    f"Rangefinder distance: {thermal.get('object_distance', '?')} m\n"
                    f"Drone AGL: {thermal.get('relative_altitude', '?')} m\n"
                    f"Canopy height: {height_m:.1f} m ({height_ft:.0f} ft)",
                )
            elif "object_distance" in thermal:
                messagebox.showinfo(
                    "Partial Data",
                    f"ObjectDistance: {thermal['object_distance']} m\n"
                    f"Gimbal pitch was {thermal.get('gimbal_pitch', '?')}deg\n\n"
                    f"Point camera straight down (-90deg) for canopy height.",
                )
            else:
                messagebox.showwarning(
                    "No Rangefinder Data",
                    "No ObjectDistance field found.\n"
                    "Enable rangefinder before taking the calibration photo.",
                )
        except ImportError:
            messagebox.showerror("Error", "sentinel_core not installed.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read thermal metadata: {e}")

    # ── Build: Scan Button ──

    def _build_scan_button(self):
        scan_frame = ttk.Frame(self._content)
        scan_frame.pack(fill="x", pady=(0, 8))

        self.scan_btn = ttk.Button(scan_frame, text="Scan Photos",
                                    command=self._on_scan, style="Accent.TButton")
        self.scan_btn.pack(side="left")

    # ── Build: Results Section (hidden until scan) ──

    def _build_results_section(self):
        self._results_frame = ttk.Frame(self._content)
        self._results_frame.pack(fill="x", pady=(0, 8))

        # Stats badges
        stats_frame = tk.Frame(self._results_frame, bg=BG_COLOR)
        stats_frame.pack(fill="x", pady=(0, 8))

        self.badge_total = StatBadge(stats_frame, "TOTAL", color=SENTINEL_DARK)
        self.badge_total.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.badge_nadir = StatBadge(stats_frame, "NADIR", color=GREEN)
        self.badge_nadir.pack(side="left", fill="x", expand=True, padx=4)

        self.badge_oblique = StatBadge(stats_frame, "OBLIQUE", color="#2980B9")
        self.badge_oblique.pack(side="left", fill="x", expand=True, padx=4)

        self.badge_panorama = StatBadge(stats_frame, "PANORAMA", color="#E67E22")
        self.badge_panorama.pack(side="left", fill="x", expand=True, padx=4)

        self.badge_platform = StatBadge(stats_frame, "PLATFORM", color=SENTINEL_MID)
        self.badge_platform.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Summary line
        self._summary_var = tk.StringVar()
        ttk.Label(self._results_frame, textvariable=self._summary_var,
                  font=(FONT_FAMILY, 9)).pack(anchor="w")

        self._output_var = tk.StringVar()
        ttk.Label(self._results_frame, textvariable=self._output_var,
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(anchor="w", pady=(2, 0))

    # ── Build: Video Panel (hidden until scan finds MP4s) ──

    def _build_video_panel(self):
        self._video_panel = ttk.LabelFrame(self._content, text="Videos found", padding=8)
        # Packed on demand by _show_video_panel

        top = ttk.Frame(self._video_panel)
        top.pack(fill="x")

        self._video_listbox = tk.Listbox(
            top, height=4, selectmode="extended",
            font=(FONT_FAMILY, 8), bg=BG_COLOR,
            relief="flat", borderwidth=1,
        )
        self._video_listbox.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(top, orient="vertical",
                           command=self._video_listbox.yview)
        sb.pack(side="left", fill="y")
        self._video_listbox.configure(yscrollcommand=sb.set)

        btn_row = ttk.Frame(self._video_panel)
        btn_row.pack(fill="x", pady=(6, 0))
        ttk.Button(
            btn_row, text="Send to Content Agent",
            command=self._on_send_to_content_agent,
            style="Accent.TButton",
        ).pack(side="left")
        self._cardinal_btn = ttk.Button(
            btn_row, text="Extract Cardinal Stills",
            command=self._on_extract_cardinal_stills,
            style="Secondary.TButton",
        )
        self._cardinal_btn.pack(side="left", padx=(6, 0))
        self._video_queue_label = ttk.Label(
            btn_row, text="", font=(FONT_FAMILY, 8), foreground=TEXT_DIM)
        self._video_queue_label.pack(side="left", padx=(10, 0))

    def _scan_videos(self, source_dir: str) -> list[dict]:
        """Return list of {path, name, has_srt} for every MP4 in source_dir."""
        src = Path(source_dir)
        # Use case-folded name as dedup key (Windows FS is case-insensitive)
        seen = set()
        found = []
        for mp4 in sorted(src.iterdir()):
            if mp4.suffix.lower() != ".mp4":
                continue
            key = mp4.name.lower()
            if key in seen:
                continue
            seen.add(key)
            srt = mp4.with_suffix(".SRT")
            if not srt.exists():
                srt = mp4.with_suffix(".srt")
            found.append({
                "path": str(mp4),
                "name": mp4.name,
                "has_srt": srt.exists(),
                "srt_path": str(srt) if srt.exists() else None,
            })
        return found

    def _show_video_panel(self, videos: list[dict]):
        self._found_videos = videos
        self._video_listbox.delete(0, "end")
        for v in videos:
            suffix = "  [+SRT]" if v["has_srt"] else ""
            self._video_listbox.insert("end", v["name"] + suffix)
        # Select all by default
        self._video_listbox.select_set(0, "end")
        self._video_queue_label.configure(text="")
        self._video_panel.pack(
            fill="x", pady=(0, 8),
            before=self._adv_toggle_frame,
        )

    def _hide_video_panel(self):
        self._found_videos = []
        self._video_panel.pack_forget()

    def _on_send_to_content_agent(self):
        if not self._found_videos:
            return
        selected = self._video_listbox.curselection()
        if not selected:
            messagebox.showwarning("Nothing selected",
                "Select at least one video from the list.", parent=self.root)
            return
        videos_to_send = [self._found_videos[i] for i in selected]

        queue_dir = SCRIPT_DIR / "video-queue"
        queue_dir.mkdir(exist_ok=True)

        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        manifest_path = queue_dir / f"{ts}_job.json"

        site = self.site_name_var.get().strip() or "Unnamed"
        manifest = {
            "created": ts,
            "site": site,
            "source_dir": self.source_var.get().strip(),
            "videos": videos_to_send,
            "targets": ["youtube", "facebook", "website"],
            "notes": (
                "Sentinel Aerial footage — edit into a 60-90s highlight reel "
                "suitable for YouTube/Facebook. Use property_overlay if KML is available."
            ),
        }

        import json as _json
        manifest_path.write_text(_json.dumps(manifest, indent=2))

        self._video_queue_label.configure(
            text=f"Queued {len(videos_to_send)} video(s) -> video-queue/{manifest_path.name}"
        )
        self._log(f"\nVideo manifest written: {manifest_path}")
        self._log(f"  {len(videos_to_send)} video(s) queued for content pipeline")
        self._log(f"  Open Claude Code and run /video-use with this manifest to edit + publish.")

    def _on_extract_cardinal_stills(self):
        """Pull N/E/S/W anchor frames from selected orbit videos via SRT yaw."""
        if not self._found_videos:
            return
        selected = self._video_listbox.curselection()
        if not selected:
            messagebox.showwarning(
                "Nothing selected",
                "Select at least one video from the list.", parent=self.root)
            return

        jobs = []
        for i in selected:
            v = self._found_videos[i]
            if not v.get("has_srt"):
                self._log(f"Cardinal stills: {v['name']} skipped — no SRT sidecar")
                continue
            if not has_yaw_telemetry(v.get("srt_path")):
                self._log(f"Cardinal stills: {v['name']} skipped — "
                          "SRT has no gimbal yaw telemetry")
                continue
            jobs.append(v)
        if not jobs:
            messagebox.showinfo(
                "No usable videos",
                "None of the selected videos have SRT gimbal telemetry.\n"
                "Cardinal stills need the .SRT sidecar recorded by the drone.",
                parent=self.root)
            return

        # Front bearing only when the profile bearing selector is on screen —
        # then labels become front/right/back/left instead of N/E/S/W.
        front_bearing = None
        try:
            if self._bearing_frame.winfo_ismapped():
                front_bearing = compass_to_bearing(self.bearing_var.get())
        except (ValueError, AttributeError, tk.TclError):
            front_bearing = None

        out_base = (self.output_var.get().strip()
                    or self.source_var.get().strip())
        out_dir = str(Path(out_base) / "video_stills")
        site = self.site_name_var.get().strip()

        self._cardinal_btn.configure(state="disabled")
        self._log(f"\nExtracting cardinal stills from {len(jobs)} video(s) "
                  f"-> {out_dir}")
        if front_bearing is not None:
            self._log(f"  Front bearing {front_bearing:.0f} deg — "
                      "labels front/right/back/left")

        def work():
            try:
                for v in jobs:
                    res = extract_cardinal_stills(
                        v["path"], v["srt_path"], out_dir,
                        front_bearing=front_bearing, site_name=site)

                    def report(v=v, res=res):
                        if res["error"]:
                            self._log(f"  {v['name']}: FAILED — {res['error']}")
                            return
                        got = ", ".join(sorted(res["stills"]))
                        self._log(
                            f"  {v['name']}: {len(res['stills'])} still(s) [{got}]"
                            f" — orbit coverage {res['coverage_deg']:.0f} deg")
                        if res["missing"]:
                            self._log(
                                f"    missing: {', '.join(res['missing'])} — "
                                "orbit never faced these directions")

                    self.root.after(0, report)
            except Exception as e:  # never leave the button dead
                self.root.after(0, self._log,
                                f"  Cardinal stills worker error: {e}")
            finally:
                self.root.after(
                    0, lambda: self._cardinal_btn.configure(state="normal"))

        threading.Thread(target=work, daemon=True).start()

    # ── Build: Advanced (collapsed) ──

    def _build_advanced_section(self):
        self._advanced_visible = False
        self._adv_toggle_frame = ttk.Frame(self._content)
        self._adv_toggle_frame.pack(fill="x", pady=(0, 4))

        self._adv_toggle_btn = ttk.Button(
            self._adv_toggle_frame, text="+ Advanced",
            command=self._toggle_advanced, style="Secondary.TButton")
        self._adv_toggle_btn.pack(side="left")

        self._adv_frame = ttk.LabelFrame(self._content, text="Advanced Settings", padding=10)
        # Not packed yet — toggled by button

        # Threshold
        thresh_row = ttk.Frame(self._adv_frame)
        thresh_row.pack(fill="x", pady=(0, 4))
        ttk.Label(thresh_row, text="Nadir threshold:").pack(side="left")
        self.threshold_var = tk.StringVar(value="-70")
        ttk.Entry(thresh_row, textvariable=self.threshold_var, width=6).pack(side="left", padx=(4, 2))
        ttk.Label(thresh_row, text="deg", font=(FONT_FAMILY, 8)).pack(side="left")

        # Bbox
        bbox_row = ttk.Frame(self._adv_frame)
        bbox_row.pack(fill="x", pady=(4, 4))
        for label_text, var_name in [("Min Lat", "min_lat"), ("Max Lat", "max_lat"),
                                      ("Min Lon", "min_lon"), ("Max Lon", "max_lon")]:
            ttk.Label(bbox_row, text=label_text, font=(FONT_FAMILY, 8)).pack(side="left", padx=(0, 2))
            var = tk.StringVar()
            setattr(self, f"_{var_name}_var", var)
            ttk.Entry(bbox_row, textvariable=var, width=11).pack(side="left", padx=(0, 6))

        # NodeODM URL
        url_row = ttk.Frame(self._adv_frame)
        url_row.pack(fill="x", pady=(4, 0))
        ttk.Label(url_row, text="NodeODM URL:").pack(side="left")
        self.nodeodm_url_var = tk.StringVar(value="http://localhost:3000")
        ttk.Entry(url_row, textvariable=self.nodeodm_url_var, width=30).pack(side="left", padx=(4, 0))

        # Reset button
        reset_row = ttk.Frame(self._adv_frame)
        reset_row.pack(fill="x", pady=(8, 0))
        ttk.Button(reset_row, text="Reset Settings",
                   command=self._on_reset, style="Secondary.TButton").pack(side="left")

    def _on_reset(self):
        """Clear saved settings and reset UI to defaults."""
        try:
            SETTINGS_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        defaults = load_settings()
        self.source_var.set("")
        self.output_var.set("")
        self.job_type_var.set(defaults["job_type"])
        self.site_name_var.set("")
        self.threshold_var.set(defaults["threshold"])
        self.nodeodm_url_var.set(defaults["nodeodm_url"])
        for name in ("min_lat", "max_lat", "min_lon", "max_lon"):
            getattr(self, f"_{name}_var").set("")
        self._classification = None
        self._working_set = None
        self._results_frame.pack_forget()
        self._hide_video_panel()
        self._action_frame.pack_forget()
        # Deliver used to be a child of _action_frame, so the pack_forget above
        # hid it too. It lives on its own step now — hide it explicitly or a
        # reset leaves a live Drive upload pointed at the previous job.
        self._deliver_btn.pack_forget()
        self._deliver_hint_var.set(
            "Run a Sort for Client first — delivery unlocks only after a "
            "client sort produces an output folder.")
        self.results_text.config(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.config(state="disabled")
        self.progress_var.set(0)
        self.badge_total.set("—")
        self.badge_nadir.set("—")
        self.badge_oblique.set("—")
        self.badge_panorama.set("—")
        self.badge_platform.set("—")
        self.status_var.set("Settings reset")

    # ── Build: Action Buttons (hidden until scan) ──

    def _build_action_buttons(self):
        self._action_frame = ttk.Frame(self._content)
        self._action_frame.pack(fill="x", pady=(0, 8))

        self.process_btn = ttk.Button(self._action_frame, text="Process",
                                       command=self._on_process, style="Accent.TButton")
        self.process_btn.pack(side="left")

        self.portfolio_btn = ttk.Button(self._action_frame, text="Portfolio Only",
                                         command=self._on_portfolio_only,
                                         style="Secondary.TButton")
        self.portfolio_btn.pack(side="left", padx=8)

        self._client_sort_btn = ttk.Button(
            self._action_frame, text="Sort for Client",
            command=self._on_client_sort, style="Accent.TButton")
        # Hidden until a profile is selected and scan is done

        self.cancel_btn = ttk.Button(self._action_frame, text="Cancel",
                                      command=self._on_cancel, style="Secondary.TButton")
        self.cancel_btn.pack(side="left", padx=4)
        self.cancel_btn.configure(state="disabled")

        ttk.Button(self._action_frame, text="Quit",
                   command=self._on_close).pack(side="right")

    # ── Build: Deliver step ──

    def _build_deliver_section(self):
        """Home for the Deliver action.

        Same button, same command, same show-after-successful-sort rule —
        it just lives on its own step now instead of trailing the action row.
        """
        deliver_frame = ttk.LabelFrame(self._content, text="Drive Delivery",
                                       padding=12)
        deliver_frame.pack(fill="x", pady=(0, 8))

        self._deliver_hint_var = tk.StringVar(
            value="Run a Sort for Client first — delivery unlocks only after a "
                  "client sort produces an output folder.")
        ttk.Label(deliver_frame, textvariable=self._deliver_hint_var,
                  font=(FONT_FAMILY, 9), foreground=TEXT_DIM,
                  wraplength=560, justify="left").pack(anchor="w")

        self._deliver_btn = ttk.Button(
            deliver_frame, text="Deliver",
            command=self._on_deliver, style="Accent.TButton")
        # Shown after a successful sort; hidden initially

    # ── Build: Progress + Log ──

    def _build_progress_section(self):
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(self._content, variable=self.progress_var,
                                             maximum=100, mode="determinate",
                                             style="Sentinel.Horizontal.TProgressbar")
        self.progress_bar.pack(fill="x", pady=(0, 8))

        results_frame = ttk.LabelFrame(self._content, text="Details", padding=6)
        results_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.results_text = tk.Text(results_frame, wrap="word", height=8,
                                     font=("Consolas", 9), state="disabled",
                                     bg=CARD_BG, fg=SENTINEL_DARK,
                                     relief="flat", padx=8, pady=6,
                                     insertbackground=SENTINEL_PURPLE,
                                     selectbackground=SENTINEL_MID,
                                     selectforeground="white")
        scrollbar = ttk.Scrollbar(results_frame, orient="vertical",
                                   command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.results_text.pack(fill="both", expand=True)

    # ── Build: Status Bar ──

    def _build_status_bar(self):
        status_frame = tk.Frame(self.root, bg=SENTINEL_DARK, padx=14, pady=5)
        status_frame.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(status_frame, textvariable=self.status_var,
                 font=(FONT_FAMILY, 8), fg="#D7BDE2",
                 bg=SENTINEL_DARK).pack(side="left")
        tk.Label(status_frame, text="Sentinel Aerial Inspections",
                 font=(FONT_FAMILY, 8), fg=TEXT_DIM,
                 bg=SENTINEL_DARK).pack(side="right")

    # ── NodeODM Check ──

    def _check_nodeodm_bg(self):
        url = self.nodeodm_url_var.get() if hasattr(self, 'nodeodm_url_var') else None
        info = check_nodeodm(url)
        self._nodeodm_ok = info is not None
        self.root.after(0, self._update_nodeodm_indicator, info)

        # Also check the splat engines: OpenSplat (primary) with MipMap
        # kept as the one-line-rollback fallback.
        self._opensplat_ok = check_opensplat()
        self._mipmap_ok = check_mipmap()
        self.root.after(0, self._update_mipmap_indicator)

    def _refresh_nodeodm_status(self):
        """Synchronous NodeODM re-probe for decision points (e.g. Process).

        The startup probe result goes stale because the stack parks between
        jobs; call this before acting on self._nodeodm_ok. Updates the
        indicator as a side effect.
        """
        url = self.nodeodm_url_var.get() if hasattr(self, 'nodeodm_url_var') else None
        info = check_nodeodm(url)
        self._nodeodm_ok = info is not None
        self._update_nodeodm_indicator(info)
        return self._nodeodm_ok

    # ── CRM mission link ──

    def _load_crm_missions_bg(self):
        """Fetch open CRM missions off the GUI thread; degrade to manual mode."""
        if not crm_sync.is_configured():
            self.root.after(0, self._crm_hint_var.set,
                            "CRM not configured — set SUPABASE_URL / SUPABASE_SERVICE_KEY "
                            "in .env to link missions. Manual mode works as always.")
            self.root.after(0, self._set_crm_status, AMBER, "CRM not configured")
            return
        missions = crm_sync.fetch_open_missions()
        self.root.after(0, self._populate_crm_dropdown, missions)

    # Callers pass a STATE, never a colour. Handing callers a raw colour is
    # what let a failed fetch paint itself green — the one thing this strip
    # exists to prevent.
    CRM_STATE_COLORS = {
        "checking": TEXT_DIM,
        "ok": GREEN,
        "unknown": AMBER,      # reachable-or-not is genuinely undecidable here
        "unconfigured": AMBER,
        "failed": RED,
    }

    def _set_crm_status(self, state, text):
        """Repaint the CRM dot in the connection strip. Verified state only."""
        color = self.CRM_STATE_COLORS.get(state, TEXT_DIM)
        self._crm_dot.delete("all")
        self._crm_dot.create_oval(1, 1, 9, 9, fill=color, outline="")
        self._crm_status_label.configure(text=text, fg=color)

    def _set_mission_chip(self, text, linked):
        """Header chip — the always-visible answer to 'which job is this?'"""
        self._mission_chip_var.set(text)
        self._mission_chip.configure(
            bg=SENTINEL_PURPLE if linked else RAIL_ACTIVE,
            fg="#FFFFFF" if linked else "#E8DAF0")

    def _populate_crm_dropdown(self, missions):
        self._crm_missions = missions
        choices = [CRM_MANUAL_CHOICE] + [m.label for m in missions]
        self._crm_combo.configure(values=choices)
        if missions:
            self._crm_hint_var.set(
                f"{len(missions)} open mission(s) in the CRM. Pick one to prefill "
                "this job and report progress back automatically.")
            self._set_crm_status("ok", f"CRM · {len(missions)} open")
        else:
            # crm_sync.fetch_open_missions() returns [] on ANY failure, so an
            # empty list does NOT prove the CRM was reached. Do not claim green.
            self._crm_hint_var.set(
                "No open missions returned (intake/scheduled/captured/uploaded). "
                "This also looks identical to a failed CRM request — check the "
                "log if you expected missions. Manual mode.")
            self._set_crm_status("unknown", "CRM · no missions returned")
        # Keep the current selection valid
        if self.crm_mission_var.get() not in choices:
            self.crm_mission_var.set(CRM_MANUAL_CHOICE)
            self._crm_job = None
            # The chip is the always-visible claim about which job this is.
            # Dropping the link without repainting it leaves the header
            # asserting a mission the app is no longer attached to.
            self._set_mission_chip("Practice / Portfolio", linked=False)

    def _on_crm_refresh(self):
        self._crm_hint_var.set("Refreshing CRM missions…")
        threading.Thread(target=self._load_crm_missions_bg, daemon=True).start()

    def _on_crm_mission_selected(self, event=None):
        choice = self.crm_mission_var.get()
        if choice == CRM_MANUAL_CHOICE:
            self._crm_job = None
            self._crm_hint_var.set("Manual mode — no CRM link for this job.")
            self._set_mission_chip("Practice / Portfolio", linked=False)
            return

        mission = next((m for m in self._crm_missions if m.label == choice), None)
        if mission is None:
            return
        self._crm_job = mission

        # Prefill job fields from the CRM record (all still editable)
        self.site_name_var.set(mission.suggested_site_name())
        if hasattr(self, "parcel_address_var") and mission.address:
            full = ", ".join(p for p in (mission.address, mission.city, mission.state) if p)
            self.parcel_address_var.set(full)
        suggested = mission.suggested_job_type()
        if suggested:
            self.job_type_var.set(suggested)

        bits = []
        if mission.client_name:
            client = mission.client_name
            if mission.client_company:
                client += f" ({mission.client_company})"
            bits.append(f"Client: {client}")
        if mission.template_name:
            code = f"{mission.path_code} — " if mission.path_code else ""
            bits.append(f"CRM job type: {code}{mission.template_name}")
        if suggested is None and mission.preset_name:
            bits.append("no sortie preset match — pick the job type manually")
        if mission.pilot_notes:
            bits.append(f"Pilot notes: {mission.pilot_notes}")
        self._crm_hint_var.set(
            f"Linked to {mission.job_number} — progress will update the CRM. "
            + " | ".join(bits))
        self._set_mission_chip(mission.job_number, linked=True)

    def _update_nodeodm_indicator(self, info):
        self._nodeodm_dot.delete("all")
        if info:
            self._nodeodm_dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
            version = info.get("version", "?")
            self._nodeodm_label.configure(text=f"NodeODM v{version}", fg=GREEN)
        else:
            self._nodeodm_dot.create_oval(1, 1, 9, 9, fill=RED, outline="")
            self._nodeodm_label.configure(text="NodeODM offline", fg=RED)

    def _update_mipmap_indicator(self):
        """Splat-engine dot: OpenSplat is the primary engine; MipMap is the
        rollback fallback and only shown when OpenSplat is unavailable."""
        self._mipmap_dot.delete("all")
        if getattr(self, "_opensplat_ok", False):
            self._mipmap_dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
            self._mipmap_label.configure(text="OpenSplat ready", fg=GREEN)
        elif self._mipmap_ok:
            self._mipmap_dot.create_oval(1, 1, 9, 9, fill="#E67E22", outline="")
            self._mipmap_label.configure(
                text="OpenSplat missing (MipMap fallback)", fg="#E67E22")
        else:
            self._mipmap_dot.create_oval(1, 1, 9, 9, fill=RED, outline="")
            self._mipmap_label.configure(text="No splat engine", fg=RED)

    # ── Job Description Update ──

    def _update_job_desc(self, *args):
        try:
            preset = get_preset(self.job_type_var.get())
            photo_info = "nadir photos only" if preset["photo_filter"] == "nadir" else "all photos"
            self._job_desc_var.set(f"{preset['description']}  |  Uses {photo_info}")
        except KeyError:
            self._job_desc_var.set("")

    def _get_selected_profile(self):
        """Return (stem, profile_dict) or (None, None) if no profile selected."""
        val = self.profile_var.get()
        if val == "None (standard sort)":
            return None, None
        for stem, display in self._profile_list:
            if display == val:
                return stem, load_profile(stem)
        return None, None

    def _on_profile_change(self, *args):
        """Update profile description and re-validate if scan data exists."""
        stem, profile = self._get_selected_profile()
        if profile is None:
            self._profile_desc_var.set("Standard nadir/oblique sort for SAI processing")
            self._profile_validation_label.pack_forget()
            self._bearing_frame.pack_forget()
            self._profile_result = None
            if hasattr(self, '_client_sort_btn'):
                self._client_sort_btn.pack_forget()
            return

        cats = profile.get("categories", [])
        cat_summary = ", ".join(
            f"{c['label']} (min {c.get('min_count', 0)})" for c in cats)
        self._profile_desc_var.set(f"{profile['name']}: {cat_summary}")

        # Show/hide bearing selector based on profile
        if profile.get("requires_front_bearing"):
            self._bearing_frame.pack(fill="x", pady=(4, 0))
        else:
            self._bearing_frame.pack_forget()

        # Re-validate against current scan if available
        if self._classification and self._classification.total > 0:
            self._run_profile_validation(profile)

    def _on_bearing_change(self, *args):
        """Re-validate when the front bearing direction changes."""
        _, profile = self._get_selected_profile()
        if profile and self._classification and self._classification.total > 0:
            self._run_profile_validation(profile)

    def _run_profile_validation(self, profile):
        """Classify scanned photos against profile and show validation."""
        from photo_classifier import classify_with_profile, compass_to_bearing
        front_bearing = None
        if profile.get("requires_front_bearing"):
            try:
                front_bearing = compass_to_bearing(self.bearing_var.get())
            except ValueError:
                front_bearing = 0
        pr = classify_with_profile(self._classification, profile,
                                   front_bearing=front_bearing)
        self._profile_result = pr

        lines = []
        for cat in pr.categories:
            status = "OK" if cat.met else "MISSING"
            lines.append(f"{cat.label}: {len(cat.photos)} photos [{status}]")
        if pr.unmatched:
            lines.append(f"Unmatched: {len(pr.unmatched)} photos")

        if pr.validation_errors:
            self._profile_validation_var.set(
                "GAPS: " + " | ".join(pr.validation_errors))
            self._profile_validation_label.configure(foreground=RED)
        else:
            self._profile_validation_var.set(
                "ALL REQUIREMENTS MET: " + " | ".join(lines))
            self._profile_validation_label.configure(foreground=GREEN)

        self._profile_validation_label.pack(anchor="w", pady=(2, 0))

        # Show or update client sort button
        if hasattr(self, '_client_sort_btn'):
            self._client_sort_btn.pack(side="left", padx=8)
        # Also log details
        self._log(f"\n--- {profile['name']} Validation ---")
        for cat in pr.categories:
            status = "OK" if cat.met else f"NEED {cat.min_count - len(cat.photos)} MORE"
            self._log(f"  {cat.label}: {len(cat.photos)} photos - {status}")
        if pr.unmatched:
            self._log(f"  Unmatched: {len(pr.unmatched)} photos")

    # ── Advanced Toggle ──

    def _toggle_advanced(self):
        if self._advanced_visible:
            self._adv_frame.pack_forget()
            self._adv_toggle_btn.configure(text="+ Advanced")
            self._advanced_visible = False
        else:
            self._adv_frame.pack(fill="x", pady=(0, 8),
                                  before=self._action_frame)
            self._adv_toggle_btn.configure(text="- Advanced")
            self._advanced_visible = True

    # ── Dialogs ──

    def _browse_source(self):
        folder = filedialog.askdirectory(title="Select folder with drone photos")
        if folder:
            self.source_var.set(folder)
            self._check_ppk_data(folder)

    def _browse_output(self):
        folder = filedialog.askdirectory(title="Select output folder for deliverables")
        if folder:
            self.output_var.set(folder)

    def _check_ppk_data(self, folder):
        """Check for RINEX/PPK data in the selected folder."""
        self._hide_ppk_banner()
        self._ppk_rinex = None
        self._ppk_corrected = False

        def check():
            rinex = detect_rinex(folder)
            if rinex:
                self.root.after(0, self._show_ppk_banner, rinex)

        threading.Thread(target=check, daemon=True).start()

    # ── Validation ──

    def _validate_scan(self):
        source = self.source_var.get().strip()
        if not source:
            messagebox.showerror("Error", "Please select a photo folder.")
            return None
        if not os.path.isdir(source):
            messagebox.showerror("Error", f"Folder not found: {source}")
            return None
        return source

    def _validate_process(self):
        site = self.site_name_var.get().strip()
        if not site:
            messagebox.showerror("Error", "Please enter a site name.")
            return None
        if not self._classification:
            messagebox.showerror("Error", "Scan photos first.")
            return None
        return site

    def _get_threshold(self):
        try:
            t = float(self.threshold_var.get())
            if -95 <= t <= 0:
                return t
        except ValueError:
            pass
        return -70.0

    def _get_bbox(self):
        vals = []
        filled = []
        for name in ["min_lat", "max_lat", "min_lon", "max_lon"]:
            raw = getattr(self, f"_{name}_var").get().strip()
            if raw:
                filled.append(name)
                try:
                    vals.append(float(raw))
                except ValueError:
                    messagebox.showwarning("Invalid BBox",
                                           f"'{raw}' is not a valid number for {name.replace('_', ' ')}.")
                    return None
            else:
                vals.append(None)
        if not filled:
            return None
        if len(filled) != 4:
            missing = [n.replace("_", " ") for n in ["min_lat", "max_lat", "min_lon", "max_lon"]
                       if n not in filled]
            messagebox.showwarning("Incomplete BBox",
                                   f"All 4 bbox fields are required. Missing: {', '.join(missing)}.\n"
                                   "Clear all fields to disable area filtering.")
            return "invalid"
        return tuple(vals)

    # ── UI Helpers ──

    def _set_running(self, running):
        self._running = running
        if running and hasattr(self, "_pages"):
            # Every long path — scan, process, portfolio, client sort, deliver —
            # writes its progress bar, streaming log, stat badges and action row
            # to the Process step. Before the stepper all of that was in one
            # scroll and always on screen; the Scan button in particular now
            # lives on Pre-Flight, so without this the operator presses it and
            # watches a page that never changes. Follow the work.
            self._show_step("process")
        state = "disabled" if running else "normal"
        self.scan_btn.configure(state=state)
        if hasattr(self, 'process_btn'):
            self.process_btn.configure(state=state)
            self.portfolio_btn.configure(state=state)
        if hasattr(self, 'cancel_btn'):
            self.cancel_btn.configure(state="normal" if running else "disabled")

    def _on_cancel(self):
        """Signal the background thread to stop polling."""
        if self._cancel_event:
            self._cancel_event.set()
            self._log("[cancel] Canceling — waiting for current operation to stop...")
            self.status_var.set("Canceling...")
            self.cancel_btn.configure(state="disabled")

    def _log(self, text):
        self.results_text.configure(state="normal")
        self.results_text.insert("end", text + "\n")
        self.results_text.see("end")
        self.results_text.configure(state="disabled")

    def _clear_log(self):
        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.configure(state="disabled")

    def _show_results(self):
        self._results_frame.pack(fill="x", pady=(0, 8),
                                  before=self._adv_toggle_frame)
        self._action_frame.pack(fill="x", pady=(0, 8),
                                 before=self.progress_bar)

    # ── Queue Polling ──

    # Stage → progress percentage milestones
    # These map pipeline stages to approximate progress bar positions
    STAGE_PROGRESS = {
        "scan": 5,
        "filtered": 15,
        "sort": 20,
        "submit": 25,
        "processing": 30,  # Base for processing; nodeodm_progress overrides with 25-90 range
        "download": 90,
        "panorama": 92,
        "report": 95,
        "complete": 100,
        "warning": None,  # Don't update bar on warnings
    }

    def _start_polling(self, msg_queue, on_done_callback):
        try:
            while True:
                msg = msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, current, total = msg
                    pct = (current / total * 100) if total > 0 else 0
                    self.progress_var.set(pct)
                    self.status_var.set(f"Scanning: {current}/{total}")
                elif kind == "stage":
                    _, stage, detail = msg
                    if stage == "nodeodm_progress":
                        try:
                            # Map NodeODM 0-100% into 25-90% of the overall bar
                            odm_pct = float(detail)
                            overall_pct = 25 + (odm_pct * 0.65)
                            self.progress_var.set(overall_pct)
                            self.status_var.set(f"Processing: {detail}%")
                        except ValueError:
                            pass
                    else:
                        # Update progress bar based on stage milestones
                        stage_pct = self.STAGE_PROGRESS.get(stage)
                        if stage_pct is not None:
                            self.progress_var.set(stage_pct)
                        self.status_var.set(f"{stage}: {detail}")
                        self._log(f"[{stage}] {detail}")
                elif kind == "done":
                    _, result = msg
                    on_done_callback(result)
                    return
                elif kind == "error":
                    _, err_msg = msg
                    messagebox.showerror("Error", err_msg)
                    self._set_running(False)
                    self.status_var.set("Error")
                    return
        except queue.Empty:
            pass
        self.root.after(100, lambda: self._start_polling(msg_queue, on_done_callback))

    # ── Scan ──

    def _on_scan(self):
        source = self._validate_scan()
        if source is None:
            return

        self._set_running(True)
        self._clear_log()
        self.progress_var.set(0)

        threshold = self._get_threshold()
        msg_queue = queue.Queue()

        def run():
            try:
                def progress(current, total, filename):
                    msg_queue.put(("progress", current, total))

                result = classify_photos(source, threshold=threshold,
                                         progress_callback=progress)
                msg_queue.put(("done", result))
            except Exception as e:
                traceback.print_exc()
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(result):
            try:
                self._classification = result
                preset = get_preset(self.job_type_var.get())
                self._working_set = scan_for_job(result, preset)

                # Update badges
                self.badge_total.set(result.total)
                self.badge_nadir.set(result.nadir_count)
                self.badge_oblique.set(result.oblique_count)
                self.badge_panorama.set(result.panorama_count)
                self.badge_platform.set((result.platform or "?").upper())

                # Summary
                self._summary_var.set(scan_summary(result, self._working_set,
                                                   preset))

                site = self.site_name_var.get().strip() or "Unnamed"
                custom_out = self.output_var.get().strip()
                if custom_out:
                    self._output_var.set(f"Output: {custom_out}")
                else:
                    from portfolio_service import build_output_dir
                    self._output_var.set(f"Output: {build_output_dir(site, job_type=self.job_type_var.get())}")

                # Auto-fill bbox from GPS bounds
                if result.gps_bounds:
                    b = result.gps_bounds
                    for name, val in [("min_lat", b[0]), ("max_lat", b[1]),
                                      ("min_lon", b[2]), ("max_lon", b[3])]:
                        var = getattr(self, f"_{name}_var")
                        if not var.get().strip():
                            var.set(f"{val:.6f}")

                # Show details
                self._log(f"Source:    {result.source_dir}")
                self._log(f"Platform:  {result.platform or 'unknown'}")
                self._log(f"Total:     {result.total} photos")
                self._log(f"  Nadir:   {result.nadir_count}")
                self._log(f"  Oblique: {result.oblique_count}")
                if result.unknown_count:
                    self._log(f"  Unknown: {result.unknown_count}")
                if result.panorama_count:
                    self._log(f"  Panoramas: {result.panorama_count} sets")
                    for ps in result.panorama_sets:
                        from pathlib import Path as _P
                        origin = "DJI pre-stitched" if ps.prestitched_path else "stitch required"
                        self._log(
                            f"    {_P(ps.folder).name}: {ps.photo_count} photos ({origin})")
                if result.panorama_stragglers:
                    skipped = sum(ps.photo_count for ps in result.panorama_stragglers)
                    self._log(f"  Panorama stragglers: {skipped} photos skipped")
                if result.gps_bounds:
                    b = result.gps_bounds
                    lat_span = (b[1] - b[0]) * 111139
                    lon_span = (b[3] - b[2]) * 111139 * 0.87
                    self._log(f"\nGPS footprint: ~{lat_span:.0f}m x {lon_span:.0f}m")

                self._show_results()

                # Video scan — show panel if any MP4s found alongside photos.
                # This read the panorama loop's temp string, so on any folder
                # WITHOUT panoramas the name was unbound and the whole results
                # display raised UnboundLocalError — the video panel (Send to
                # Content Agent / Extract Cardinal Stills) never appeared at all.
                videos = self._scan_videos(result.source_dir)
                if videos:
                    self._show_video_panel(videos)
                    self._log(f"\nVideos found: {len(videos)}")
                    for v in videos:
                        tag = " [+SRT]" if v["has_srt"] else ""
                        self._log(f"  {v['name']}{tag}")
                else:
                    self._hide_video_panel()

                self.status_var.set(f"Scan complete — {result.total} photos, "
                                     f"{self._working_set.total} selected for {preset['label']}")

                # Run profile validation if a client profile is selected
                _, profile = self._get_selected_profile()
                if profile:
                    self._run_profile_validation(profile)
            except Exception as e:
                self._log(f"\nError: {e}")
                self.status_var.set("Error displaying results")
            finally:
                self._set_running(False)

        self._start_polling(msg_queue, on_done)

    # ── Process (NodeODM) ──

    def _on_process(self):
        site = self._validate_process()
        if site is None:
            return

        job_type = self.job_type_var.get()
        preset = get_preset(job_type)
        engine = preset.get("engine", "nodeodm")

        if engine == "mipmap" and not self._mipmap_ok:
            messagebox.showerror("MipMap Not Found",
                "MipMap Desktop is not installed at the expected path.\n\n"
                "Install MipMap Desktop or use a different job type.")
            return

        # Splat jobs need BOTH: NodeODM (SfM poses) and the OpenSplat image.
        if engine == "opensplat" and not getattr(self, "_opensplat_ok", False):
            messagebox.showerror("OpenSplat Not Available",
                "The OpenSplat docker image was not found in WSL.\n\n"
                "Run start-nodeodm.ps1 to boot the stack, or see\n"
                "opensplat-status.md in the vault if the image needs a rebuild.")
            return

        if engine_requires_nodeodm(engine) and not self._nodeodm_ok:
            # The startup probe goes stale: the WSL/NodeODM stack is parked
            # between jobs (on-demand since 2026-07-21), so a red dot here is
            # the NORMAL state, and submit_to_nodeodm() already auto-boots the
            # stack (recovery task + 150s wait) before submitting. Re-probe in
            # case the stack came up after launch, then offer to proceed —
            # refusing here made every mission start with a false error.
            self._refresh_nodeodm_status()
        if engine_requires_nodeodm(engine) and not self._nodeodm_ok:
            if not messagebox.askyesno("NodeODM Parked",
                    "NodeODM is not running — the WSL stack parks itself "
                    "between jobs.\n\n"
                    "It will be started automatically when processing begins; "
                    "the first submit can take a few extra minutes while the "
                    "stack boots.\n\n"
                    "Start processing?"):
                return

        # Check minimum photo count
        min_photos = preset.get("min_photos", 20)
        if engine == "local" and self._classification:
            photo_count = sum(
                ps.photo_count for ps in self._classification.panorama_sets)
        else:
            photo_count = self._working_set.total if self._working_set else 0
        if photo_count < min_photos:
            if not messagebox.askyesno("Low Photo Count",
                    f"This job type ({preset['label']}) needs at least {min_photos} photos "
                    f"but only {photo_count} were selected.\n\n"
                    f"Processing will likely fail or produce poor results.\n\n"
                    f"Continue anyway?"):
                return

        self._cancel_event = threading.Event()
        self._set_running(True)
        self._clear_log()
        self.progress_var.set(0)

        source = self.source_var.get().strip()
        threshold = self._get_threshold()
        bbox = self._get_bbox()
        if bbox == "invalid":
            self._set_running(False)
            return
        base_url = self.nodeodm_url_var.get().strip() or None
        custom_output = self.output_var.get().strip() or None
        msg_queue = queue.Queue()
        cancel_event = self._cancel_event
        crm_job = self._crm_job  # snapshot — GUI selection may change mid-run

        def run():
            try:
                def progress_cb(stage, detail):
                    if stage == "task_submitted" and crm_job:
                        # Persist the task handle immediately — a crash during
                        # the multi-hour poll then leaves a reattachable id
                        if crm_sync.record_task_id(crm_job.id, detail):
                            msg_queue.put(("stage", "crm",
                                           f"task id {str(detail)[:8]} → CRM"))
                    msg_queue.put(("stage", stage, detail))

                if crm_job:
                    if crm_sync.mark_processing(crm_job.id, photo_count=photo_count):
                        progress_cb("crm", f"{crm_job.job_number} → processing")
                    else:
                        progress_cb("crm", "CRM update failed (continuing)")

                result = process_job(
                    source_dir=source,
                    job_type=job_type,
                    site_name=site,
                    threshold=threshold,
                    bbox=bbox,
                    base_url=base_url,
                    progress_callback=progress_cb,
                    output_dir=custom_output,
                    cancel_event=cancel_event,
                )

                if crm_job and "error" not in result:
                    if crm_sync.mark_complete(crm_job.id, result):
                        progress_cb("crm", f"{crm_job.job_number} → complete")
                    else:
                        progress_cb("crm", "CRM update failed (continuing)")
                    report_id = crm_sync.push_report(crm_job, result)
                    if report_id:
                        progress_cb("crm", f"report draft created in CRM "
                                           f"({crm_job.job_number})")
                    else:
                        progress_cb("crm", "no CRM report draft (skipped or failed)")
                elif crm_job:
                    crm_sync.mark_failed(crm_job.id, result.get("error", "unknown"))

                msg_queue.put(("done", result))
            except Exception as e:
                traceback.print_exc()
                if crm_job:
                    crm_sync.mark_failed(crm_job.id, e)
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(result):
            try:
                self.progress_var.set(100)

                if "error" in result:
                    self._log(f"\nError: {result['error']}")
                    self.status_var.set(f"Failed: {result['error']}")
                else:
                    output_dir = result.get("output_dir", "")
                    downloaded = result.get("downloaded", {})
                    self._log(f"\nOutput: {output_dir}")
                    self._log(f"Downloaded: {len(downloaded)} files")
                    for name, path in downloaded.items():
                        size = os.path.getsize(path) / (1024 * 1024) if os.path.exists(path) else 0
                        self._log(f"  {name} ({size:.1f} MB)")
                    self.status_var.set(f"Complete — {len(downloaded)} outputs in {output_dir}")
            except Exception as e:
                self._log(f"\nError: {e}")
                self.status_var.set(f"Error: {e}")
            finally:
                self._set_running(False)

        self._start_polling(msg_queue, on_done)

    # ── Portfolio Only (local sort) ──

    def _on_portfolio_only(self):
        site = self._validate_process()
        if site is None:
            return

        self._set_running(True)
        self._clear_log()
        self.progress_var.set(0)

        source = self.source_var.get().strip()
        job_type = self.job_type_var.get()
        threshold = self._get_threshold()
        bbox = self._get_bbox()
        if bbox == "invalid":
            self._set_running(False)
            return
        custom_output = self.output_var.get().strip() or None
        msg_queue = queue.Queue()

        def run():
            try:
                def progress_cb(stage, detail):
                    msg_queue.put(("stage", stage, detail))

                result = portfolio_only(
                    source_dir=source,
                    job_type=job_type,
                    site_name=site,
                    threshold=threshold,
                    bbox=bbox,
                    progress_callback=progress_cb,
                    output_dir=custom_output,
                )
                msg_queue.put(("done", result))
            except Exception as e:
                traceback.print_exc()
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(result):
            try:
                self.progress_var.set(100)
                if "error" in result:
                    self._log(f"\nError: {result['error']}")
                    self.status_var.set(f"Failed: {result['error']}")
                else:
                    output_dir = result.get("output_dir", "")
                    wset = result.get("working_set")
                    self._log(f"\nPhotos sorted locally")
                    self._log(f"Output: {output_dir}")
                    if wset:
                        self._log(f"  Nadir:   {wset.nadir_count}")
                        self._log(f"  Oblique: {wset.oblique_count}")
                    self.status_var.set(f"Portfolio sorted — {output_dir}")
            except Exception as e:
                self._log(f"\nError: {e}")
            finally:
                self._set_running(False)

        self._start_polling(msg_queue, on_done)


    # ── Sort for Client (profile-based) ──

    def _on_client_sort(self):
        """Sort and rename photos according to the selected client profile."""
        stem, profile = self._get_selected_profile()
        if profile is None:
            messagebox.showwarning("No Profile", "Select a client profile first.")
            return

        if not self._classification or self._classification.total == 0:
            messagebox.showwarning("No Scan", "Scan photos first.")
            return

        site = self.site_name_var.get().strip()
        if not site:
            messagebox.showerror("Error", "Please enter a site name (used for file renaming).")
            return

        # Determine output directory
        custom_out = self.output_var.get().strip()
        if custom_out:
            out_dir = custom_out
        else:
            out_dir = str(Path(PORTFOLIO_ROOT) / site / stem)

        # Get front bearing if needed
        front_bearing = None
        if profile.get("requires_front_bearing"):
            try:
                from photo_classifier import compass_to_bearing
                front_bearing = compass_to_bearing(self.bearing_var.get())
            except ValueError:
                messagebox.showerror("Error", "Invalid front bearing direction.")
                return

        # Confirm with user
        pr = classify_with_profile(self._classification, profile,
                                   front_bearing=front_bearing)
        cat_lines = "\n".join(
            f"  {c.label}: {len(c.photos)} photos"
            f"{' (MISSING ' + str(c.min_count - len(c.photos)) + ')' if not c.met else ''}"
            for c in pr.categories
        )
        msg = (
            f"Sort {self._classification.total} photos for {profile['name']}?\n\n"
            f"{cat_lines}\n"
            f"{'Unmatched: ' + str(len(pr.unmatched)) if pr.unmatched else ''}\n\n"
            f"Files will be copied and renamed to:\n{out_dir}\n\n"
            f"Rename pattern: {profile.get('rename_pattern', 'N/A')}"
        )
        if not messagebox.askyesno("Sort for Client", msg):
            return

        self._set_running(True)
        self._clear_log()
        self.progress_var.set(0)
        msg_queue = queue.Queue()

        classification = self._classification

        def run():
            try:
                def progress(current, total, filename):
                    msg_queue.put(("progress", current, total))

                pr = sort_with_profile(
                    classification, profile, out_dir,
                    site_name=site, copy=True,
                    progress_callback=progress,
                    front_bearing=front_bearing)
                msg_queue.put(("done", pr))
            except Exception as e:
                traceback.print_exc()
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(pr):
            try:
                self.progress_var.set(100)
                self._log(f"\n--- {pr.profile_name} Sort Complete ---")
                self._log(f"Output: {out_dir}")
                for cat in pr.categories:
                    self._log(f"  {cat.label}: {len(cat.photos)} photos")
                if pr.unmatched:
                    self._log(f"  Unmatched: {len(pr.unmatched)} → _unmatched/")

                if pr.validation_errors:
                    self._log(f"\nWARNING — Missing shots:")
                    for err in pr.validation_errors:
                        self._log(f"  {err}")
                    self.status_var.set(f"Sorted with gaps — review before submitting")
                else:
                    self._log(f"\nAll requirements met!")
                    self.status_var.set(f"Client sort complete — {out_dir}")

                # Enable delivery button (now lives on the Deliver step)
                self._last_sort_output = out_dir
                self._deliver_hint_var.set(f"Ready to deliver:  {out_dir}")
                self._deliver_btn.pack(anchor="w", pady=(10, 0))

                if messagebox.askyesno("Sort Complete",
                        f"Photos sorted to:\n{out_dir}\n\nOpen folder?"):
                    os.startfile(out_dir)
            except Exception as e:
                self._log(f"\nError: {e}")
            finally:
                self._set_running(False)

        self._start_polling(msg_queue, on_done)


    # ── Google Drive Delivery ──

    def _on_deliver(self):
        """Upload the last sort output to Google Drive and generate a share link."""
        if not self._last_sort_output or not Path(self._last_sort_output).exists():
            messagebox.showwarning("No Output",
                "No sorted output to deliver. Run a client sort first.")
            return

        site = self.site_name_var.get().strip() or "Delivery"
        folder_name = f"SAI — {site}"

        # Check auth. Transient refresh failures are NOT a disconnect —
        # only a missing/revoked grant should open the sign-in browser.
        try:
            drive_ready = is_authenticated()
        except DriveUnavailableError as e:
            self._log(f"Google Drive: {e}")
            messagebox.showwarning("Drive Temporarily Unreachable", str(e))
            self.status_var.set("Drive unreachable — try again shortly")
            return
        if not drive_ready:
            self._log("Google Drive: not authenticated — opening browser...")
            self.status_var.set("Waiting for Google sign-in...")
            try:
                authenticate()
                self._log("Google Drive: authenticated!")
            except Exception as e:
                messagebox.showerror("Auth Failed",
                    f"Could not sign into Google Drive:\n{e}\n\n"
                    "Make sure google_client_id and google_client_secret "
                    "are set in sortie_settings.json or as environment variables.")
                self.status_var.set("Drive auth failed")
                return

        if not messagebox.askyesno("Deliver to Google Drive",
                f"Upload all files from:\n{self._last_sort_output}\n\n"
                f"To Drive folder: \"{folder_name}\"\n"
                f"A share link will be generated."):
            return

        self._set_running(True)
        self._clear_log()
        self._log(f"Uploading to Google Drive: {folder_name}")
        self.progress_var.set(0)
        msg_queue = queue.Queue()

        output_dir = self._last_sort_output

        def run():
            try:
                def progress(current, total, filename, pct):
                    msg_queue.put(("progress", current, total))
                    msg_queue.put(("stage", "upload",
                                   f"{current}/{total}: {filename}"))

                result = drive_deliver(
                    output_dir, folder_name,
                    progress_callback=progress)
                msg_queue.put(("done", result))
            except Exception as e:
                import traceback
                traceback.print_exc()
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        crm_job = self._crm_job

        def on_done(result):
            try:
                self.progress_var.set(100)
                link = result["share_link"]
                count = result["file_count"]
                self._log(f"\n--- Delivery Complete ---")
                self._log(f"Files uploaded: {count}")
                self._log(f"Share link: {link}")
                self.status_var.set("Delivered!")

                if crm_job:
                    def push_link():
                        ok = crm_sync.record_delivery(crm_job.id, link)
                        msg = (f"CRM: Drive link saved to {crm_job.job_number}"
                               if ok else "CRM: could not save Drive link")
                        self.root.after(0, self._log, msg)
                    threading.Thread(target=push_link, daemon=True).start()

                # Copy link to clipboard
                self.root.clipboard_clear()
                self.root.clipboard_append(link)

                messagebox.showinfo("Delivered",
                    f"{count} files uploaded to Google Drive.\n\n"
                    f"Share link (copied to clipboard):\n{link}")
            except Exception as e:
                self._log(f"\nError: {e}")
            finally:
                self._set_running(False)

        self._start_polling(msg_queue, on_done)

    def _open_property_highlights(self):
        source_dir = self._settings.get("source_dir", "")
        PropertyHighlightsDialog(self.root, source_dir=source_dir)


# ─── PROPERTY HIGHLIGHTS DIALOG ─────────────────────────────────────────────

class PropertyHighlightsDialog(tk.Toplevel):
    SENTINEL_PURPLE = "#5B2C6F"
    BG_COLOR        = "#F7F5F9"
    ACCENT_GOLD     = "#F4D03F"
    TEXT_DIM        = "#7D6B8A"
    FONT_FAMILY     = "Segoe UI"

    def __init__(self, parent, source_dir=""):
        super().__init__(parent)
        self.title("Property Highlights")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.configure(bg=self.BG_COLOR)

        self._cancel_flag = threading.Event()
        self._thread = None

        self._build_ui(source_dir)
        self.update_idletasks()
        self._center()

    def _center(self):
        self.update_idletasks()
        pw = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        pw2 = self.master.winfo_width()
        py2 = self.master.winfo_height()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"+{pw + pw2//2 - w//2}+{py + py2//2 - h//2}")

    def _build_ui(self, source_dir):
        # ── Purple header ──
        hdr = tk.Frame(self, bg=self.SENTINEL_PURPLE)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text="Property Highlights Overlay",
            font=(self.FONT_FAMILY, 14, "bold"),
            fg="white", bg=self.SENTINEL_PURPLE, pady=10,
        ).pack()

        body = tk.Frame(self, bg=self.BG_COLOR, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        lbl_kw = dict(bg=self.BG_COLOR, font=(self.FONT_FAMILY, 9), anchor="w")
        row = 0

        # Video file
        tk.Label(body, text="Drone Video (MP4):", **lbl_kw).grid(
            row=row, column=0, sticky="w", pady=(0, 2))
        row += 1
        self._video_var = tk.StringVar()
        vid_frame = tk.Frame(body, bg=self.BG_COLOR)
        vid_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        body.columnconfigure(0, weight=1)
        vid_entry = ttk.Entry(vid_frame, textvariable=self._video_var, width=44)
        vid_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(vid_frame, text="Browse…", command=self._browse_video).pack(
            side="left", padx=(4, 0))
        row += 1

        # KML file
        tk.Label(body, text="Property KML:", **lbl_kw).grid(
            row=row, column=0, sticky="w", pady=(0, 2))
        row += 1
        self._kml_var = tk.StringVar()
        kml_frame = tk.Frame(body, bg=self.BG_COLOR)
        kml_frame.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        kml_entry = ttk.Entry(kml_frame, textvariable=self._kml_var, width=44)
        kml_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(kml_frame, text="Browse…", command=self._browse_kml).pack(
            side="left", padx=(4, 0))
        row += 1

        # Output file
        tk.Label(body, text="Output Video:", **lbl_kw).grid(
            row=row, column=0, sticky="w", pady=(0, 2))
        row += 1
        self._out_var = tk.StringVar()
        out_frame = tk.Frame(body, bg=self.BG_COLOR)
        out_frame.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        ttk.Entry(out_frame, textvariable=self._out_var, width=44).pack(
            side="left", fill="x", expand=True)
        ttk.Button(out_frame, text="Browse…", command=self._browse_output).pack(
            side="left", padx=(4, 0))
        row += 1

        # Options row
        opts = tk.Frame(body, bg=self.BG_COLOR)
        opts.grid(row=row, column=0, sticky="w", pady=(0, 10))
        row += 1

        tk.Label(opts, text="Heading override (° or blank=auto):",
                 **lbl_kw).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self._heading_var = tk.StringVar()
        ttk.Entry(opts, textvariable=self._heading_var, width=6).grid(
            row=0, column=1, padx=(0, 14))

        tk.Label(opts, text="Preview scale:", **lbl_kw).grid(
            row=0, column=2, sticky="w", padx=(0, 6))
        self._scale_var = tk.StringVar(value="2")
        ttk.Combobox(
            opts, textvariable=self._scale_var,
            values=["1 (4K)", "2 (half-res)", "4 (quarter-res)"],
            state="readonly", width=14,
        ).grid(row=0, column=3, padx=(0, 14))

        self._label_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Address label",
                        variable=self._label_var).grid(row=0, column=4)

        # Progress bar
        self._progress = ttk.Progressbar(body, mode="determinate", length=440)
        self._progress.grid(row=row, column=0, sticky="ew", pady=(0, 4))
        row += 1

        self._status_var = tk.StringVar(value="Ready")
        tk.Label(body, textvariable=self._status_var,
                 font=(self.FONT_FAMILY, 8), fg=self.TEXT_DIM,
                 bg=self.BG_COLOR, anchor="w").grid(
            row=row, column=0, sticky="w", pady=(0, 8))
        row += 1

        # Buttons
        btn_frame = tk.Frame(body, bg=self.BG_COLOR)
        btn_frame.grid(row=row, column=0, sticky="e")
        self._run_btn = ttk.Button(btn_frame, text="Run", command=self._run)
        self._run_btn.pack(side="left", padx=(0, 6))
        self._cancel_btn = ttk.Button(btn_frame, text="Cancel",
                                       command=self._cancel, state="disabled")
        self._cancel_btn.pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="left")

        # Pre-fill source dir
        if source_dir:
            self._video_var.set(source_dir)

    # ── File pickers ──

    def _browse_video(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select drone video",
            filetypes=[("MP4 video", "*.mp4 *.MP4"), ("All files", "*.*")],
        )
        if path:
            self._video_var.set(path)
            # Auto-match KML
            matched = find_matching_kml(path)
            if matched and not self._kml_var.get():
                self._kml_var.set(matched)
                self._status_var.set(f"KML auto-matched: {Path(matched).name}")
            # Auto-set output path
            if not self._out_var.get():
                p = Path(path)
                self._out_var.set(str(p.parent / (p.stem + "_highlights.mp4")))

    def _browse_kml(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select property KML",
            filetypes=[("KML files", "*.kml *.KML"), ("All files", "*.*")],
        )
        if path:
            self._kml_var.set(path)

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save highlighted video as",
            defaultextension=".mp4",
            filetypes=[("MP4 video", "*.mp4")],
        )
        if path:
            self._out_var.set(path)

    # ── Run / Cancel ──

    def _run(self):
        video = self._video_var.get().strip()
        kml   = self._kml_var.get().strip()
        out   = self._out_var.get().strip()

        if not video or not Path(video).is_file():
            messagebox.showerror("Missing video", "Select a valid drone MP4.", parent=self)
            return
        if not kml or not Path(kml).is_file():
            messagebox.showerror("Missing KML", "Select a valid property KML.", parent=self)
            return
        if not out:
            messagebox.showerror("Missing output", "Specify an output file path.", parent=self)
            return

        heading = None
        raw_h = self._heading_var.get().strip()
        if raw_h:
            try:
                heading = float(raw_h)
            except ValueError:
                messagebox.showerror("Bad heading",
                    "Heading must be a number (0-360) or blank.", parent=self)
                return

        scale_raw = self._scale_var.get()
        scale_down = int(scale_raw.split()[0])

        self._cancel_flag.clear()
        self._run_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal")
        self._progress["value"] = 0
        self._status_var.set("Starting render…")

        def progress_cb(current, total):
            if total > 0:
                pct = current / total * 100
                self._progress["value"] = pct
                self._status_var.set(f"Frame {current}/{total}  ({pct:.0f}%)")
                self.update_idletasks()

        def worker():
            try:
                result = render_highlights(
                    video, kml, out,
                    heading_override=heading,
                    scale_down=scale_down,
                    show_label=self._label_var.get(),
                    progress_cb=progress_cb,
                    cancel_flag=self._cancel_flag,
                )
                self.after(0, self._on_done, result, None)
            except Exception as exc:
                self.after(0, self._on_done, None, exc)

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()

    def _cancel(self):
        self._cancel_flag.set()
        self._status_var.set("Cancelling…")
        self._cancel_btn.configure(state="disabled")

    def _on_done(self, result, error):
        self._run_btn.configure(state="normal")
        self._cancel_btn.configure(state="disabled")
        if error:
            self._status_var.set(f"Error: {error}")
            messagebox.showerror("Render failed", str(error), parent=self)
        elif result:
            self._progress["value"] = 100
            self._status_var.set(f"Done -> {Path(result).name}")
            messagebox.showinfo(
                "Done",
                f"Highlights video saved:\n{result}",
                parent=self,
            )


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    PortfolioMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
