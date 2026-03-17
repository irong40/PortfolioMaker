"""
Sentinel Portfolio Maker — Desktop Application v2.0

Intent-driven GUI: pick a job type, scan photos, process via NodeODM
or sort locally. Produces client-ready deliverable packages.

Usage:
    python portfolio_maker.py
    (or double-click the desktop shortcut)
"""

import os
import sys
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# Ensure our own modules are importable
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from photo_classifier import classify_photos, PIPELINE_AVAILABLE
from odm_presets import JOB_TYPES, get_preset
from portfolio_service import (
    check_nodeodm, scan_for_job, process_job, portfolio_only, PORTFOLIO_ROOT,
)
from mipmap_service import check_mipmap

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
ICON_FILE = SCRIPT_DIR / "portfolio_maker.ico"


# ─── CUSTOM STYLES ─────────────────────────────────────────────────────────

def configure_styles():
    style = ttk.Style()
    style.theme_use("clam")

    style.configure(".", font=(FONT_FAMILY, 9), background=BG_COLOR)
    style.configure("TFrame", background=BG_COLOR)
    style.configure("TLabel", background=BG_COLOR, font=(FONT_FAMILY, 9))
    style.configure("TLabelframe", background=BG_COLOR)
    style.configure("TLabelframe.Label", background=BG_COLOR,
                    font=(FONT_FAMILY, 9, "bold"), foreground=SENTINEL_PURPLE)

    style.configure("Accent.TButton", font=(FONT_FAMILY, 10, "bold"), padding=(16, 8))
    style.map("Accent.TButton",
              background=[("active", SENTINEL_MID), ("!active", SENTINEL_PURPLE)],
              foreground=[("active", "white"), ("!active", "white")])

    style.configure("Secondary.TButton", font=(FONT_FAMILY, 9), padding=(12, 6))

    style.configure("Sentinel.Horizontal.TProgressbar",
                    troughcolor="#E8E0ED", background=SENTINEL_PURPLE, thickness=8)

    style.configure("TEntry", padding=4)
    style.configure("TCheckbutton", background=BG_COLOR, font=(FONT_FAMILY, 9))
    style.configure("TRadiobutton", background=BG_COLOR, font=(FONT_FAMILY, 9))


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
        self.root.title("Sentinel Portfolio Maker")
        self.root.configure(bg=BG_COLOR)
        self.root.resizable(True, True)
        self.root.minsize(760, 780)

        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except tk.TclError:
                pass

        self._classification = None
        self._working_set = None
        self._running = False
        self._nodeodm_ok = False
        self._mipmap_ok = False

        configure_styles()
        self._build_header()
        self._build_input_section()
        self._build_scan_button()
        self._build_results_section()
        self._build_advanced_section()
        self._build_action_buttons()
        self._build_progress_section()
        self._build_status_bar()
        self._center_window()

        # Hide results and actions until scan
        self._results_frame.pack_forget()
        self._action_frame.pack_forget()

        # Check NodeODM in background
        threading.Thread(target=self._check_nodeodm_bg, daemon=True).start()

    def _center_window(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - 390
        y = (self.root.winfo_screenheight() // 2) - 400
        self.root.geometry(f"780x800+{x}+{y}")

    # ── Build: Header ──

    def _build_header(self):
        header = tk.Frame(self.root, bg=SENTINEL_PURPLE, padx=20, pady=14)
        header.pack(fill="x")

        title_row = tk.Frame(header, bg=SENTINEL_PURPLE)
        title_row.pack(fill="x")

        tk.Label(title_row, text="Portfolio Maker",
                 font=(FONT_FAMILY, 18, "bold"), fg="white",
                 bg=SENTINEL_PURPLE).pack(side="left")

        tk.Label(title_row, text="SENTINEL",
                 font=(FONT_FAMILY, 9, "bold"), fg=ACCENT_GOLD,
                 bg=SENTINEL_PURPLE).pack(side="left", padx=(8, 0), pady=(6, 0))

        # NodeODM status indicator
        self._nodeodm_frame = tk.Frame(title_row, bg=SENTINEL_PURPLE)
        self._nodeodm_frame.pack(side="right")
        self._nodeodm_dot = tk.Canvas(self._nodeodm_frame, width=10, height=10,
                                       bg=SENTINEL_PURPLE, highlightthickness=0)
        self._nodeodm_dot.pack(side="left", padx=(0, 4))
        self._nodeodm_dot.create_oval(1, 1, 9, 9, fill=TEXT_DIM, outline="")
        self._nodeodm_label = tk.Label(self._nodeodm_frame, text="NodeODM",
                                        font=(FONT_FAMILY, 8), fg=TEXT_DIM,
                                        bg=SENTINEL_PURPLE)
        self._nodeodm_label.pack(side="left")

        # MipMap status indicator
        self._mipmap_frame = tk.Frame(title_row, bg=SENTINEL_PURPLE)
        self._mipmap_frame.pack(side="right", padx=(0, 12))
        self._mipmap_dot = tk.Canvas(self._mipmap_frame, width=10, height=10,
                                      bg=SENTINEL_PURPLE, highlightthickness=0)
        self._mipmap_dot.pack(side="left", padx=(0, 4))
        self._mipmap_dot.create_oval(1, 1, 9, 9, fill=TEXT_DIM, outline="")
        self._mipmap_label = tk.Label(self._mipmap_frame, text="MipMap",
                                       font=(FONT_FAMILY, 8), fg=TEXT_DIM,
                                       bg=SENTINEL_PURPLE)
        self._mipmap_label.pack(side="left")

        tk.Label(header, text="Select job type  |  Scan photos  |  Process or sort",
                 font=(FONT_FAMILY, 9), fg="#D7BDE2",
                 bg=SENTINEL_PURPLE).pack(anchor="w")

        engine = "drone-pipeline" if PIPELINE_AVAILABLE else "standalone"
        tk.Label(header, text=f"Engine: {engine}",
                 font=(FONT_FAMILY, 8), fg=SENTINEL_MID,
                 bg=SENTINEL_PURPLE).pack(anchor="w")

    # ── Build: Input Section (folder + job type + site name) ──

    def _build_input_section(self):
        self._content = ttk.Frame(self.root)
        self._content.pack(fill="both", expand=True, padx=14, pady=(10, 0))

        # Photo folder
        src_frame = ttk.LabelFrame(self._content, text="Photo Folder", padding=10)
        src_frame.pack(fill="x", pady=(0, 8))

        row = ttk.Frame(src_frame)
        row.pack(fill="x")
        self.source_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.source_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse_source,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))

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

        self.badge_platform = StatBadge(stats_frame, "PLATFORM", color=SENTINEL_MID)
        self.badge_platform.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Summary line
        self._summary_var = tk.StringVar()
        ttk.Label(self._results_frame, textvariable=self._summary_var,
                  font=(FONT_FAMILY, 9)).pack(anchor="w")

        self._output_var = tk.StringVar()
        ttk.Label(self._results_frame, textvariable=self._output_var,
                  font=(FONT_FAMILY, 8), foreground=TEXT_DIM).pack(anchor="w", pady=(2, 0))

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

        ttk.Button(self._action_frame, text="Quit",
                   command=self.root.quit).pack(side="right")

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

        # Also check MipMap
        self._mipmap_ok = check_mipmap()
        self.root.after(0, self._update_mipmap_indicator)

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
        self._mipmap_dot.delete("all")
        if self._mipmap_ok:
            self._mipmap_dot.create_oval(1, 1, 9, 9, fill=GREEN, outline="")
            self._mipmap_label.configure(text="MipMap installed", fg=GREEN)
        else:
            self._mipmap_dot.create_oval(1, 1, 9, 9, fill=RED, outline="")
            self._mipmap_label.configure(text="MipMap not found", fg=RED)

    # ── Job Description Update ──

    def _update_job_desc(self, *args):
        try:
            preset = get_preset(self.job_type_var.get())
            photo_info = "nadir photos only" if preset["photo_filter"] == "nadir" else "all photos"
            self._job_desc_var.set(f"{preset['description']}  |  Uses {photo_info}")
        except KeyError:
            self._job_desc_var.set("")

    # ── Advanced Toggle ──

    def _toggle_advanced(self):
        if self._advanced_visible:
            self._adv_frame.pack_forget()
            self._adv_toggle_btn.configure(text="+ Advanced")
            self._advanced_visible = False
        else:
            self._adv_frame.pack(in_=self._content, fill="x", pady=(0, 8),
                                  before=self._action_frame)
            self._adv_toggle_btn.configure(text="- Advanced")
            self._advanced_visible = True

    # ── Dialogs ──

    def _browse_source(self):
        folder = filedialog.askdirectory(title="Select folder with drone photos")
        if folder:
            self.source_var.set(folder)

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
                    return None
            else:
                vals.append(None)
        if not filled:
            return None
        if len(filled) != 4:
            return None
        return tuple(vals)

    # ── UI Helpers ──

    def _set_running(self, running):
        self._running = running
        state = "disabled" if running else "normal"
        self.scan_btn.configure(state=state)
        if hasattr(self, 'process_btn'):
            self.process_btn.configure(state=state)
            self.portfolio_btn.configure(state=state)

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
        self._results_frame.pack(in_=self._content, fill="x", pady=(0, 8),
                                  before=self._adv_toggle_frame)
        self._action_frame.pack(in_=self._content, fill="x", pady=(0, 8),
                                 before=self.progress_bar)

    # ── Queue Polling ──

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
                self.badge_platform.set((result.platform or "?").upper())

                # Summary
                photo_filter = preset["photo_filter"]
                if photo_filter:
                    self._summary_var.set(
                        f"Using: {self._working_set.total} {photo_filter} photos ({preset['label']} preset)")
                else:
                    self._summary_var.set(
                        f"Using: {self._working_set.total} photos — all ({preset['label']} preset)")

                site = self.site_name_var.get().strip() or "Unnamed"
                from portfolio_service import build_output_dir
                self._output_var.set(f"Output: {build_output_dir(site)}")

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
                if result.gps_bounds:
                    b = result.gps_bounds
                    lat_span = (b[1] - b[0]) * 111139
                    lon_span = (b[3] - b[2]) * 111139 * 0.87
                    self._log(f"\nGPS footprint: ~{lat_span:.0f}m x {lon_span:.0f}m")

                self._show_results()
                self.status_var.set(f"Scan complete — {result.total} photos, "
                                     f"{self._working_set.total} selected for {preset['label']}")
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

        if engine != "mipmap" and not self._nodeodm_ok:
            messagebox.showerror("NodeODM Offline",
                "NodeODM is not reachable. Start Docker or check the URL in Advanced settings.\n\n"
                "Use 'Portfolio Only' to sort photos without processing.")
            return

        self._set_running(True)
        self._clear_log()
        self.progress_var.set(0)
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start(20)

        source = self.source_var.get().strip()
        threshold = self._get_threshold()
        bbox = self._get_bbox()
        base_url = self.nodeodm_url_var.get().strip() or None
        msg_queue = queue.Queue()

        def run():
            try:
                def progress_cb(stage, detail):
                    msg_queue.put(("stage", stage, detail))

                result = process_job(
                    source_dir=source,
                    job_type=job_type,
                    site_name=site,
                    threshold=threshold,
                    bbox=bbox,
                    base_url=base_url,
                    progress_callback=progress_cb,
                )
                msg_queue.put(("done", result))
            except Exception as e:
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(result):
            try:
                self.progress_bar.stop()
                self.progress_bar.configure(mode="determinate")
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
                )
                msg_queue.put(("done", result))
            except Exception as e:
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


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    PortfolioMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
