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
    write_manifest,
    PIPELINE_AVAILABLE,
)

# ─── COLORS / STYLE ────────────────────────────────────────────────────────

SENTINEL_PURPLE = "#5B2C6F"
SENTINEL_LIGHT = "#F4ECF7"
BG_COLOR = "#FAFAFA"
FONT_FAMILY = "Segoe UI"


# ─── MAIN APPLICATION ──────────────────────────────────────────────────────

class PortfolioMakerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Sentinel — Portfolio Maker")
        self.root.configure(bg=BG_COLOR)
        self.root.resizable(True, True)
        self.root.minsize(650, 520)

        self._result = None
        self._running = False

        self._build_ui()
        self._center_window()

    def _center_window(self):
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        x = (self.root.winfo_screenwidth() // 2) - (w // 2)
        y = (self.root.winfo_screenheight() // 2) - (h // 2)
        self.root.geometry(f"700x560+{x}+{y}")

    def _build_ui(self):
        # ── Header
        header = tk.Frame(self.root, bg=SENTINEL_PURPLE, padx=15, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="Sentinel Portfolio Maker",
                 font=(FONT_FAMILY, 16, "bold"), fg="white",
                 bg=SENTINEL_PURPLE).pack(anchor="w")
        tk.Label(header, text="Sort drone photos by gimbal angle. Produce portfolio assets fast.",
                 font=(FONT_FAMILY, 9), fg=SENTINEL_LIGHT,
                 bg=SENTINEL_PURPLE).pack(anchor="w")

        pipeline_status = "drone-pipeline loaded" if PIPELINE_AVAILABLE else "fallback mode"
        tk.Label(header, text=f"Engine: {pipeline_status}",
                 font=(FONT_FAMILY, 8), fg="#D7BDE2",
                 bg=SENTINEL_PURPLE).pack(anchor="w")

        # ── Source folder
        src_frame = ttk.LabelFrame(self.root, text="Photo Folder", padding=10)
        src_frame.pack(fill="x", padx=12, pady=(12, 6))

        row = ttk.Frame(src_frame)
        row.pack(fill="x")
        self.source_var = tk.StringVar()
        self.source_entry = ttk.Entry(row, textvariable=self.source_var, width=60)
        self.source_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=self._browse_source).pack(side="left", padx=(5, 0))

        # ── Settings
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=10)
        settings_frame.pack(fill="x", padx=12, pady=6)

        # Threshold
        thresh_row = ttk.Frame(settings_frame)
        thresh_row.pack(fill="x", pady=2)
        ttk.Label(thresh_row, text="Nadir threshold (degrees):").pack(side="left")
        self.threshold_var = tk.StringVar(value="-70")
        thresh_entry = ttk.Entry(thresh_row, textvariable=self.threshold_var, width=8)
        thresh_entry.pack(side="left", padx=(5, 10))
        ttk.Label(thresh_row, text="Photos from -95 to this value = nadir",
                  font=(FONT_FAMILY, 8)).pack(side="left")

        # Options row
        opts_row = ttk.Frame(settings_frame)
        opts_row.pack(fill="x", pady=2)

        self.copy_var = tk.BooleanVar(value=True)
        ttk.Radiobutton(opts_row, text="Copy (keep originals)", variable=self.copy_var,
                        value=True).pack(side="left", padx=(0, 15))
        ttk.Radiobutton(opts_row, text="Move (relocate originals)", variable=self.copy_var,
                        value=False).pack(side="left")

        opts_row2 = ttk.Frame(settings_frame)
        opts_row2.pack(fill="x", pady=2)
        self.manifest_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts_row2, text="Write manifest.json with full metadata",
                        variable=self.manifest_var).pack(anchor="w")

        # ── Action buttons
        btn_frame = ttk.Frame(self.root, padding=(12, 6))
        btn_frame.pack(fill="x")

        self.scan_btn = ttk.Button(btn_frame, text="Scan Only (Preview)",
                                    command=self._on_scan)
        self.scan_btn.pack(side="left")

        self.sort_btn = ttk.Button(btn_frame, text="Sort Photos",
                                    command=self._on_sort)
        self.sort_btn.pack(side="left", padx=8)

        ttk.Button(btn_frame, text="Quit", command=self.root.quit).pack(side="right")

        # ── Progress
        prog_frame = ttk.Frame(self.root, padding=(12, 4))
        prog_frame.pack(fill="x")
        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(prog_frame, variable=self.progress_var,
                                             maximum=100, mode="determinate")
        self.progress_bar.pack(fill="x")

        # ── Results
        results_frame = ttk.LabelFrame(self.root, text="Results", padding=10)
        results_frame.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        self.results_text = tk.Text(results_frame, wrap="word", height=12,
                                     font=(FONT_FAMILY, 9), state="disabled",
                                     bg="#FFFFFF", relief="flat")
        scrollbar = ttk.Scrollbar(results_frame, orient="vertical",
                                   command=self.results_text.yview)
        self.results_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.results_text.pack(fill="both", expand=True)

        # ── Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var,
                               relief="sunken", padding=(8, 3))
        status_bar.pack(fill="x", side="bottom")

    def _browse_source(self):
        folder = filedialog.askdirectory(title="Select folder with drone photos")
        if folder:
            self.source_var.set(folder)

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

    def _set_buttons(self, enabled):
        state = "normal" if enabled else "disabled"
        self.scan_btn.configure(state=state)
        self.sort_btn.configure(state=state)
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
        self.root.update_idletasks()

    # ── Scan (classify only, no file operations) ──

    def _on_scan(self):
        source, threshold = self._validate()
        if source is None:
            return

        self._set_buttons(False)
        self._clear_log()
        self.progress_var.set(0)
        self.status_var.set("Scanning...")

        def run():
            try:
                def progress(current, total, filename):
                    self.root.after(0, lambda: self._update_progress(current, total))
                    self.root.after(0, lambda: self.status_var.set(
                        f"Reading metadata: {current}/{total}"))

                result = classify_photos(source, threshold=threshold,
                                         progress_callback=progress)
                self._result = result

                def done():
                    self._show_results(result)
                    if self.manifest_var.get():
                        path = write_manifest(result)
                        self._log(f"\nManifest: {path}")
                    self.status_var.set(f"Scan complete — {result.total} photos classified")
                    self._set_buttons(True)

                self.root.after(0, done)

            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
                self.root.after(0, lambda: self._set_buttons(True))
                self.root.after(0, lambda: self.status_var.set("Error"))

        threading.Thread(target=run, daemon=True).start()

    # ── Sort (classify + copy/move files) ──

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
        self.status_var.set("Classifying...")

        def run():
            try:
                # Phase 1: Classify
                def classify_progress(current, total, filename):
                    self.root.after(0, lambda: self._update_progress(current, total))
                    self.root.after(0, lambda: self.status_var.set(
                        f"Reading metadata: {current}/{total}"))

                result = classify_photos(source, threshold=threshold,
                                         progress_callback=classify_progress)

                if result.total == 0:
                    self.root.after(0, lambda: messagebox.showinfo("Done", "No photos found."))
                    self.root.after(0, lambda: self._set_buttons(True))
                    return

                # Phase 2: Sort
                action = "Copying" if copy else "Moving"
                self.root.after(0, lambda: self.status_var.set(f"{action} files..."))
                self.root.after(0, lambda: self.progress_var.set(0))

                def sort_progress(current, total, filename):
                    self.root.after(0, lambda: self._update_progress(current, total))
                    self.root.after(0, lambda: self.status_var.set(
                        f"{action}: {current}/{total}"))

                result = sort_photos(result, copy=copy,
                                     progress_callback=sort_progress)
                self._result = result

                def done():
                    self._show_results(result)
                    if self.manifest_var.get():
                        path = write_manifest(result)
                        self._log(f"\nManifest: {path}")
                    action_past = "copied" if copy else "moved"
                    self.status_var.set(
                        f"Done — {result.nadir_count} nadir, "
                        f"{result.oblique_count} oblique {action_past}")
                    self._set_buttons(True)

                self.root.after(0, done)

            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
                self.root.after(0, lambda: self._set_buttons(True))
                self.root.after(0, lambda: self.status_var.set("Error"))

        threading.Thread(target=run, daemon=True).start()

    # ── Display results ──

    def _show_results(self, result):
        self._clear_log()
        self._log(f"Source:    {result.source_dir}")
        self._log(f"Platform:  {result.platform or 'unknown'}")
        self._log(f"Threshold: {result.threshold} degrees")
        self._log("")
        self._log(f"Total:     {result.total} photos")
        self._log(f"Nadir:     {result.nadir_count} (straight down)")
        self._log(f"Oblique:   {result.oblique_count} (angled)")
        if result.unknown_count:
            self._log(f"Unknown:   {result.unknown_count} (no pitch data)")

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

        if result.nadir_dir:
            self._log("")
            self._log(f"Output:")
            self._log(f"  Nadir:   {result.nadir_dir}")
            self._log(f"  Oblique: {result.oblique_dir}")

        self._log("")
        self._log("Next steps:")
        self._log("  Orthophoto/DSM/volume → process nadir folder in WebODM")
        self._log("  3D model → process all photos from original folder")


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    PortfolioMakerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
