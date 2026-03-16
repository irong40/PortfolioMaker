"""
Sentinel Portfolio Maker — Desktop Application

Tkinter GUI for sorting drone photos into nadir/oblique and producing
portfolio-ready outputs. Reuses drone-pipeline EXIF/XMP extraction.

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

from photo_classifier import (
    classify_photos,
    sort_photos,
    filter_photos,
    export_photos,
    write_manifest,
    PIPELINE_AVAILABLE,
)

# ─── COLORS / STYLE ────────────────────────────────────────────────────────

SENTINEL_PURPLE = "#5B2C6F"
SENTINEL_DARK = "#1A0A2E"
SENTINEL_LIGHT = "#F4ECF7"
SENTINEL_MID = "#AF7AC5"
ACCENT_GOLD = "#F4D03F"
BG_COLOR = "#F7F5F9"
CARD_BG = "#FFFFFF"
TEXT_DIM = "#7D6B8A"
FONT_FAMILY = "Segoe UI"
ICON_FILE = SCRIPT_DIR / "portfolio_maker.ico"


# ─── CUSTOM STYLES ─────────────────────────────────────────────────────────

def configure_styles():
    style = ttk.Style()
    style.theme_use("clam")

    # General
    style.configure(".", font=(FONT_FAMILY, 9), background=BG_COLOR)
    style.configure("TFrame", background=BG_COLOR)
    style.configure("TLabel", background=BG_COLOR, font=(FONT_FAMILY, 9))
    style.configure("TLabelframe", background=BG_COLOR)
    style.configure("TLabelframe.Label", background=BG_COLOR,
                    font=(FONT_FAMILY, 9, "bold"), foreground=SENTINEL_PURPLE)

    # Buttons
    style.configure("Accent.TButton", font=(FONT_FAMILY, 10, "bold"),
                    padding=(16, 8))
    style.map("Accent.TButton",
              background=[("active", SENTINEL_MID), ("!active", SENTINEL_PURPLE)],
              foreground=[("active", "white"), ("!active", "white")])

    style.configure("Secondary.TButton", font=(FONT_FAMILY, 9), padding=(12, 6))

    # Progress bar
    style.configure("Sentinel.Horizontal.TProgressbar",
                    troughcolor="#E8E0ED", background=SENTINEL_PURPLE,
                    thickness=8)

    # Entry
    style.configure("TEntry", padding=4)

    # Checkbutton / Radiobutton
    style.configure("TCheckbutton", background=BG_COLOR, font=(FONT_FAMILY, 9))
    style.configure("TRadiobutton", background=BG_COLOR, font=(FONT_FAMILY, 9))


# ─── STAT BADGE WIDGET ────────────────────────────────────────────────────

class StatBadge(tk.Frame):
    """A small labeled number badge for the results summary."""

    def __init__(self, parent, label, value="—", color=SENTINEL_PURPLE, **kwargs):
        super().__init__(parent, bg=CARD_BG, padx=12, pady=8, **kwargs)
        self._value_var = tk.StringVar(value=str(value))
        self._label_var = tk.StringVar(value=label)

        tk.Label(self, textvariable=self._value_var,
                 font=(FONT_FAMILY, 20, "bold"), fg=color, bg=CARD_BG).pack()
        tk.Label(self, textvariable=self._label_var,
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
        self.root.minsize(720, 680)

        # Set icon
        if ICON_FILE.exists():
            try:
                self.root.iconbitmap(str(ICON_FILE))
            except tk.TclError:
                pass

        self._result = None
        self._running = False

        configure_styles()
        self._build_ui()
        self._center_window()

    def _center_window(self):
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - 380
        y = (self.root.winfo_screenheight() // 2) - 370
        self.root.geometry(f"760x740+{x}+{y}")

    def _build_ui(self):
        # ── Header bar ──
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

        tk.Label(header, text="Sort drone photos by gimbal angle  |  Filter by area  |  Export for any deliverable",
                 font=(FONT_FAMILY, 9), fg="#D7BDE2",
                 bg=SENTINEL_PURPLE).pack(anchor="w")

        engine = "drone-pipeline" if PIPELINE_AVAILABLE else "standalone"
        tk.Label(header, text=f"Engine: {engine}",
                 font=(FONT_FAMILY, 8), fg=SENTINEL_MID,
                 bg=SENTINEL_PURPLE).pack(anchor="w")

        # ── Main content area (scrollable-friendly padding) ──
        content = ttk.Frame(self.root)
        content.pack(fill="both", expand=True, padx=14, pady=(10, 0))

        # ── Source folder ──
        src_frame = ttk.LabelFrame(content, text="Photo Folder", padding=10)
        src_frame.pack(fill="x", pady=(0, 8))

        row = ttk.Frame(src_frame)
        row.pack(fill="x")
        self.source_var = tk.StringVar()
        ttk.Entry(row, textvariable=self.source_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse_source,
                   style="Secondary.TButton").pack(side="left", padx=(6, 0))

        # ── Settings row ──
        settings_frame = ttk.LabelFrame(content, text="Classification", padding=10)
        settings_frame.pack(fill="x", pady=(0, 8))

        # Threshold + copy/move on same row
        top_row = ttk.Frame(settings_frame)
        top_row.pack(fill="x", pady=(0, 4))

        ttk.Label(top_row, text="Nadir threshold:").pack(side="left")
        self.threshold_var = tk.StringVar(value="-70")
        ttk.Entry(top_row, textvariable=self.threshold_var, width=6).pack(side="left", padx=(4, 2))
        ttk.Label(top_row, text="deg", font=(FONT_FAMILY, 8)).pack(side="left", padx=(0, 20))

        self.copy_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(top_row, text="Copy", variable=self.copy_var,
                        value=True).pack(side="left", padx=(0, 4))
        ttk.Radiobutton(top_row, text="Move", variable=self.copy_var,
                        value=False).pack(side="left")

        self.manifest_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top_row, text="Manifest", variable=self.manifest_var).pack(side="right")

        # ── Area filter ──
        filter_frame = ttk.LabelFrame(content, text="Area Filter (optional — subset by GPS)", padding=10)
        filter_frame.pack(fill="x", pady=(0, 8))

        bbox_row = ttk.Frame(filter_frame)
        bbox_row.pack(fill="x")

        for label_text, var_name in [("Min Lat", "min_lat"), ("Max Lat", "max_lat"),
                                      ("Min Lon", "min_lon"), ("Max Lon", "max_lon")]:
            ttk.Label(bbox_row, text=label_text, font=(FONT_FAMILY, 8)).pack(side="left", padx=(0, 2))
            var = tk.StringVar()
            setattr(self, f"_{var_name}_var", var)
            ttk.Entry(bbox_row, textvariable=var, width=12).pack(side="left", padx=(0, 8))

        filter_opts = ttk.Frame(filter_frame)
        filter_opts.pack(fill="x", pady=(6, 0))

        self.filter_type_var = tk.StringVar(value="all")
        ttk.Radiobutton(filter_opts, text="All", variable=self.filter_type_var,
                        value="all").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(filter_opts, text="Nadir only", variable=self.filter_type_var,
                        value="nadir").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(filter_opts, text="Oblique only", variable=self.filter_type_var,
                        value="oblique").pack(side="left")

        self.export_var = tk.StringVar()
        ttk.Label(filter_opts, text="Export to:", font=(FONT_FAMILY, 8)).pack(side="left", padx=(20, 4))
        ttk.Entry(filter_opts, textvariable=self.export_var, width=20).pack(side="left")
        ttk.Button(filter_opts, text="...", width=3,
                   command=self._browse_export).pack(side="left", padx=(2, 0))

        # ── Action buttons ──
        btn_frame = ttk.Frame(content)
        btn_frame.pack(fill="x", pady=(0, 8))

        self.scan_btn = ttk.Button(btn_frame, text="Scan (Preview)",
                                    command=self._on_scan, style="Secondary.TButton")
        self.scan_btn.pack(side="left")

        self.sort_btn = ttk.Button(btn_frame, text="Sort Photos",
                                    command=self._on_sort, style="Accent.TButton")
        self.sort_btn.pack(side="left", padx=8)

        self.export_btn = ttk.Button(btn_frame, text="Export Filtered",
                                      command=self._on_export, style="Secondary.TButton")
        self.export_btn.pack(side="left")

        ttk.Button(btn_frame, text="Quit", command=self.root.quit).pack(side="right")

        # ── Progress ──
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(content, variable=self.progress_var,
                                             maximum=100, mode="determinate",
                                             style="Sentinel.Horizontal.TProgressbar")
        self.progress_bar.pack(fill="x", pady=(0, 8))

        # ── Stats badges row ──
        stats_frame = tk.Frame(content, bg=BG_COLOR)
        stats_frame.pack(fill="x", pady=(0, 8))

        self.badge_total = StatBadge(stats_frame, "TOTAL", color=SENTINEL_DARK)
        self.badge_total.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.badge_nadir = StatBadge(stats_frame, "NADIR", color="#27AE60")
        self.badge_nadir.pack(side="left", fill="x", expand=True, padx=4)

        self.badge_oblique = StatBadge(stats_frame, "OBLIQUE", color="#2980B9")
        self.badge_oblique.pack(side="left", fill="x", expand=True, padx=4)

        self.badge_platform = StatBadge(stats_frame, "PLATFORM", color=SENTINEL_MID)
        self.badge_platform.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ── Results log ──
        results_frame = ttk.LabelFrame(content, text="Details", padding=6)
        results_frame.pack(fill="both", expand=True, pady=(0, 8))

        self.results_text = tk.Text(results_frame, wrap="word", height=10,
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

        # ── Status bar ──
        status_frame = tk.Frame(self.root, bg=SENTINEL_DARK, padx=14, pady=5)
        status_frame.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(status_frame, textvariable=self.status_var,
                 font=(FONT_FAMILY, 8), fg="#D7BDE2",
                 bg=SENTINEL_DARK).pack(side="left")
        tk.Label(status_frame, text="Sentinel Aerial Inspections",
                 font=(FONT_FAMILY, 8), fg=TEXT_DIM,
                 bg=SENTINEL_DARK).pack(side="right")

    # ── Dialogs ──

    def _browse_source(self):
        folder = filedialog.askdirectory(title="Select folder with drone photos")
        if folder:
            self.source_var.set(folder)

    def _browse_export(self):
        folder = filedialog.askdirectory(title="Select export destination folder")
        if folder:
            self.export_var.set(folder)

    # ── Validation ──

    def _validate(self):
        source = self.source_var.get().strip()
        if not source:
            messagebox.showerror("Error", "Please select a photo folder.")
            return None, None
        if not os.path.isdir(source):
            messagebox.showerror("Error", f"Folder not found: {source}")
            return None, None

        try:
            threshold = float(self.threshold_var.get())
        except ValueError:
            messagebox.showerror("Error", "Threshold must be a number (e.g. -70)")
            return None, None

        if threshold > 0 or threshold < -95:
            messagebox.showerror("Error", "Threshold should be between -95 and 0")
            return None, None

        return source, threshold

    def _get_bbox(self):
        """Parse bbox fields. Returns tuple or None."""
        vals = []
        for name in ["min_lat", "max_lat", "min_lon", "max_lon"]:
            raw = getattr(self, f"_{name}_var").get().strip()
            if not raw:
                return None
            try:
                vals.append(float(raw))
            except ValueError:
                messagebox.showerror("Error", f"Invalid {name}: {raw}")
                return "error"
        return tuple(vals)

    def _get_filter_type(self):
        v = self.filter_type_var.get()
        return v if v != "all" else None

    # ── UI helpers ──

    def _set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        self.scan_btn.configure(state=state)
        self.sort_btn.configure(state=state)
        self.export_btn.configure(state=state)
        self._running = not enabled

    def _log(self, text):
        self.results_text.configure(state="normal")
        self.results_text.insert("end", text + "\n")
        self.results_text.see("end")
        self.results_text.configure(state="disabled")

    def _clear_log(self):
        self.results_text.configure(state="normal")
        self.results_text.delete("1.0", "end")
        self.results_text.configure(state="disabled")

    def _update_progress(self, current, total):
        pct = (current / total * 100) if total > 0 else 0
        self.progress_var.set(pct)

    def _update_badges(self, result):
        self.badge_total.set(result.total)
        self.badge_nadir.set(result.nadir_count)
        self.badge_oblique.set(result.oblique_count)
        self.badge_platform.set((result.platform or "?").upper())

    # ── Queue-based thread communication ──
    # Tkinter is not thread-safe. We use a queue to pass messages from
    # worker threads to the main thread, polled every 100ms.

    def _start_polling(self, msg_queue, on_done_callback):
        """Poll the message queue from the main thread."""
        try:
            while True:
                msg = msg_queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, current, total = msg
                    self._update_progress(current, total)
                    self.status_var.set(f"Reading metadata: {current}/{total}")
                elif kind == "sort_progress":
                    _, current, total, action = msg
                    self._update_progress(current, total)
                    self.status_var.set(f"{action}: {current}/{total}")
                elif kind == "export_progress":
                    _, current, total = msg
                    self._update_progress(current, total)
                    self.status_var.set(f"Exporting: {current}/{total}")
                elif kind == "done":
                    _, result = msg
                    on_done_callback(result)
                    return  # stop polling
                elif kind == "error":
                    _, err_msg = msg
                    messagebox.showerror("Error", err_msg)
                    self._set_buttons(True)
                    self.status_var.set("Error")
                    return  # stop polling
        except queue.Empty:
            pass
        self.root.after(100, lambda: self._start_polling(msg_queue, on_done_callback))

    # ── Classify (shared by scan/sort/export) ──

    def _classify(self, source, threshold, callback):
        """Run classification in a thread, then call callback(result) on the main thread."""
        msg_queue = queue.Queue()

        def run():
            try:
                def progress(current, total, filename):
                    msg_queue.put(("progress", current, total))

                result = classify_photos(source, threshold=threshold,
                                         progress_callback=progress)
                self._result = result
                msg_queue.put(("done", result))

            except Exception as e:
                msg_queue.put(("error", str(e)))

        threading.Thread(target=run, daemon=True).start()

        def on_done(result):
            # Auto-fill bbox fields from GPS bounds on first scan
            if result.gps_bounds:
                b = result.gps_bounds
                for name, val in [("min_lat", b[0]), ("max_lat", b[1]),
                                  ("min_lon", b[2]), ("max_lon", b[3])]:
                    var = getattr(self, f"_{name}_var")
                    if not var.get().strip():
                        var.set(f"{val:.6f}")
            callback(result)

        self._start_polling(msg_queue, on_done)

    # ── Scan ──

    def _on_scan(self):
        source, threshold = self._validate()
        if source is None:
            return

        self._set_buttons(False)
        self._clear_log()
        self.progress_var.set(0)

        def on_done(result):
            try:
                self._show_results(result)
                self._update_badges(result)
                if self.manifest_var.get():
                    try:
                        path = write_manifest(result)
                        self._log(f"\nManifest: {path}")
                    except (OSError, PermissionError) as e:
                        self._log(f"\nManifest: could not write to source folder ({e})")
                        self._log("  (SD card may be read-only — manifest skipped)")
                self.status_var.set(f"Scan complete — {result.total} photos classified")
            except Exception as e:
                self._log(f"\nError displaying results: {e}")
                self.status_var.set("Error displaying results")
            finally:
                self._set_buttons(True)

        self._classify(source, threshold, on_done)

    # ── Sort ──

    def _on_sort(self):
        source, threshold = self._validate()
        if source is None:
            return

        copy = self.copy_var.get()
        if not copy:
            if not messagebox.askyesno("Confirm Move",
                    "Move mode will relocate your original files.\n"
                    "Are you sure? (Copy mode is safer)"):
                return

        self._set_buttons(False)
        self._clear_log()
        self.progress_var.set(0)

        def on_classified(result):
            if result.total == 0:
                messagebox.showinfo("Done", "No photos found.")
                self._set_buttons(True)
                return

            action = "Copying" if copy else "Moving"
            self.status_var.set(f"{action} files...")
            self.progress_var.set(0)

            sort_queue = queue.Queue()

            def do_sort():
                try:
                    def progress(current, total, filename):
                        sort_queue.put(("sort_progress", current, total, action))

                    sorted_result = sort_photos(result, copy=copy,
                                                progress_callback=progress)
                    sort_queue.put(("done", sorted_result))
                except Exception as e:
                    sort_queue.put(("error", str(e)))

            threading.Thread(target=do_sort, daemon=True).start()

            def on_sort_done(sorted_result):
                try:
                    self._show_results(sorted_result)
                    self._update_badges(sorted_result)
                    if self.manifest_var.get():
                        try:
                            path = write_manifest(sorted_result)
                            self._log(f"\nManifest: {path}")
                        except (OSError, PermissionError) as e:
                            self._log(f"\nManifest: could not write ({e})")
                    past = "copied" if copy else "moved"
                    self.status_var.set(
                        f"Done — {sorted_result.nadir_count} nadir, "
                        f"{sorted_result.oblique_count} oblique {past}")
                except Exception as e:
                    self.status_var.set(f"Error: {e}")
                finally:
                    self._set_buttons(True)

            self._start_polling(sort_queue, on_sort_done)

        self._classify(source, threshold, on_classified)

    # ── Export filtered subset ──

    def _on_export(self):
        source, threshold = self._validate()
        if source is None:
            return

        export_dir = self.export_var.get().strip()
        if not export_dir:
            messagebox.showerror("Error", "Set an export destination folder first.")
            return

        bbox = self._get_bbox()
        if bbox == "error":
            return
        filter_type = self._get_filter_type()

        if not bbox and not filter_type:
            if not messagebox.askyesno("No Filter",
                    "No area or type filter set — this will export ALL photos.\nContinue?"):
                return

        self._set_buttons(False)
        self._clear_log()
        self.progress_var.set(0)

        def on_classified(result):
            if result.total == 0:
                messagebox.showinfo("Done", "No photos found.")
                self._set_buttons(True)
                return

            # Apply filters
            filtered = filter_photos(result, bbox=bbox, classification=filter_type)

            if filtered.total == 0:
                messagebox.showinfo("No Match", "No photos match the filter criteria.")
                self._set_buttons(True)
                return

            self.status_var.set(f"Exporting {filtered.total} photos...")
            self.progress_var.set(0)

            export_queue = queue.Queue()
            export_manifest_path = [None]  # mutable container for closure

            def do_export():
                try:
                    def progress(current, total, filename):
                        export_queue.put(("export_progress", current, total))

                    out = export_photos(filtered, export_dir, copy=True,
                                        progress_callback=progress)

                    if self.manifest_var.get():
                        export_manifest_path[0] = write_manifest(filtered, Path(out) / "manifest.json")

                    export_queue.put(("done", out))
                except Exception as e:
                    export_queue.put(("error", str(e)))

            threading.Thread(target=do_export, daemon=True).start()

            def on_export_done(out):
                try:
                    self._update_badges(filtered)
                    self._clear_log()
                    self._log(f"Exported {filtered.total} photos to:")
                    self._log(f"  {out}")
                    if bbox:
                        self._log(f"\nArea filter: {bbox[0]:.6f},{bbox[2]:.6f} to {bbox[1]:.6f},{bbox[3]:.6f}")
                    if filter_type:
                        self._log(f"Type filter: {filter_type}")
                    self._log(f"\nBreakdown:")
                    self._log(f"  Nadir:   {filtered.nadir_count}")
                    self._log(f"  Oblique: {filtered.oblique_count}")
                    if export_manifest_path[0]:
                        self._log(f"\nManifest: {export_manifest_path[0]}")
                    self.status_var.set(f"Exported {filtered.total} photos")
                except Exception as e:
                    self.status_var.set(f"Error: {e}")
                finally:
                    self._set_buttons(True)

            self._start_polling(export_queue, on_export_done)

        self._classify(source, threshold, on_classified)

    # ── Display results ──

    def _show_results(self, result):
        self._clear_log()
        self._log(f"Source:    {result.source_dir}")
        self._log(f"Platform:  {result.platform or 'unknown'}")
        self._log(f"Threshold: {result.threshold} degrees")
        self._log("")
        self._log(f"Total:     {result.total} photos")
        self._log(f"  Nadir:   {result.nadir_count} (straight down)")
        self._log(f"  Oblique: {result.oblique_count} (angled)")
        if result.unknown_count:
            self._log(f"  Unknown: {result.unknown_count} (no pitch data)")

        if result.pitch_min is not None:
            self._log("")
            self._log(f"Pitch range: {result.pitch_min:.1f} to {result.pitch_max:.1f} degrees")

            nadir_pitches = [p.pitch for p in result.photos
                            if p.classification == "nadir" and p.pitch is not None]
            oblique_pitches = [p.pitch for p in result.photos
                              if p.classification == "oblique" and p.pitch is not None]
            if nadir_pitches:
                self._log(f"  Nadir:   {min(nadir_pitches):.1f} to {max(nadir_pitches):.1f}")
            if oblique_pitches:
                self._log(f"  Oblique: {min(oblique_pitches):.1f} to {max(oblique_pitches):.1f}")

        if result.gps_bounds:
            b = result.gps_bounds
            lat_span = (b[1] - b[0]) * 111139
            lon_span = (b[3] - b[2]) * 111139 * 0.87
            self._log(f"\nGPS footprint: ~{lat_span:.0f}m x {lon_span:.0f}m")
            self._log(f"  {b[0]:.6f}, {b[2]:.6f}  to  {b[1]:.6f}, {b[3]:.6f}")

        if result.nadir_dir:
            self._log("")
            self._log(f"Output folders:")
            self._log(f"  Nadir:   {result.nadir_dir}")
            self._log(f"  Oblique: {result.oblique_dir}")

        self._log("")
        self._log("Next steps:")
        self._log("  Orthophoto/DSM/volume  ->  nadir folder in WebODM")
        self._log("  3D model              ->  all photos from original folder")
        self._log("  Filtered export       ->  set area + type filter above, click Export")


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    PortfolioMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
