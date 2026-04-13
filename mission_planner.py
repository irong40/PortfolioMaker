"""
Sortie — Mission Planner

Tkinter dialog for generating autonomous DJI mission KMZ files.
Currently supports: Bees360 (1 nadir + 8 birdseye orbit, matching Sortie's
bees360.json profile defaults).

Subprocesses drone-pipeline's bees360_kmz.py for the actual KMZ generation
so there's a single source of truth for the WPML schema and parcel lookup.

Usage from Sortie:
    from mission_planner import MissionPlannerDialog
    MissionPlannerDialog(parent_root, settings_dict, save_settings_fn)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

DEFAULT_DRONE_PIPELINE_PATH = r"C:\Users\redle.SOULAAN\Documents\drone-pipeline"
DEFAULT_OUTPUT_DIR = r"E:\Sentinel\Missions"
DEFAULT_ALT_FT = "25"
DEFAULT_ORBIT_RADIUS_FT = "25"

# Mirror of bees360_kmz.PROPERTY_PRESETS + BEES360_CLEARANCE_FT.
# Values are (display_label, building_height_ft).
# Mission altitude = building_height + BEES360_CLEARANCE_FT (30).
# Orbit radius defaults to altitude (keeps camera framing on centroid at -45 deg pitch).
BEES360_CLEARANCE_FT = 30
PROPERTY_PRESETS = [
    ("— custom (use fields below) —", None),
    ("Ranch / 1-story  (~12 ft roof → 42 ft alt)", 12),
    ("2-story  (~25 ft roof → 55 ft alt)", 25),
    ("3-story  (~35 ft roof → 65 ft alt)", 35),
    ("Small commercial  (~30 ft → 60 ft alt)", 30),
    ("Large commercial  (~45 ft → 75 ft alt)", 45),
]

# Sortie color palette (kept in sync with sortie.py)
SENTINEL_PURPLE = "#5B2C6F"
SENTINEL_LIGHT = "#F4ECF7"
ACCENT_GOLD = "#F4D03F"
BG_COLOR = "#F7F5F9"
TEXT_DIM = "#7D6B8A"
GREEN = "#27AE60"
RED = "#E74C3C"
FONT_FAMILY = "Segoe UI"


class MissionPlannerDialog(tk.Toplevel):
    """Modal dialog for generating an autonomous mission KMZ."""

    def __init__(self, parent: tk.Misc, settings: dict, save_settings_cb=None):
        super().__init__(parent)
        self.title("Generate Bees360 Mission")
        self.configure(bg=BG_COLOR)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._settings = settings
        self._save_cb = save_settings_cb
        self._last_output_path: Path | None = None
        self._running = False

        self._build_ui()
        self._center_on_parent(parent)
        self.address_entry.focus_set()

    # ── Layout ──

    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg=SENTINEL_PURPLE, padx=16, pady=10)
        header.pack(fill="x")
        tk.Label(header, text="Generate Bees360 Mission",
                 font=(FONT_FAMILY, 13, "bold"), fg="white",
                 bg=SENTINEL_PURPLE).pack(anchor="w")
        tk.Label(header, text="Autonomous KMZ for DJI Mini 4 Pro  |  1 nadir + 8 birdseye orbit",
                 font=(FONT_FAMILY, 8), fg="#D7BDE2",
                 bg=SENTINEL_PURPLE).pack(anchor="w")

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        # Address / coords section
        addr_frame = ttk.LabelFrame(body, text="Property Location", padding=10)
        addr_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(addr_frame, text="Address:").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.address_var = tk.StringVar()
        self.address_entry = ttk.Entry(addr_frame, textvariable=self.address_var, width=52)
        self.address_entry.grid(row=0, column=1, columnspan=3, sticky="ew", pady=(0, 4))

        ttk.Label(addr_frame, text="or Lat/Lon:").grid(row=1, column=0, sticky="w", pady=(0, 4))
        self.lat_var = tk.StringVar()
        self.lon_var = tk.StringVar()
        ttk.Entry(addr_frame, textvariable=self.lat_var, width=14).grid(row=1, column=1, sticky="w")
        ttk.Entry(addr_frame, textvariable=self.lon_var, width=14).grid(row=1, column=2, sticky="w", padx=(4, 0))
        ttk.Label(addr_frame, text="Label:").grid(row=1, column=3, sticky="e", padx=(8, 4))
        self.label_var = tk.StringVar()
        ttk.Entry(addr_frame, textvariable=self.label_var, width=14).grid(row=1, column=4, sticky="w")

        ttk.Label(addr_frame, text="Provide an address (preferred) or lat/lon override with optional label.",
                  foreground=TEXT_DIM, font=(FONT_FAMILY, 8)).grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(2, 0))

        addr_frame.grid_columnconfigure(1, weight=1)

        # Mission parameters
        params_frame = ttk.LabelFrame(body, text="Mission Parameters", padding=10)
        params_frame.pack(fill="x", pady=(0, 8))

        # Property Type preset (auto-fills altitude + radius on selection)
        ttk.Label(params_frame, text="Property Type:").grid(row=0, column=0, sticky="w")
        self.property_type_var = tk.StringVar(value=PROPERTY_PRESETS[0][0])
        property_combo = ttk.Combobox(
            params_frame, textvariable=self.property_type_var,
            values=[p[0] for p in PROPERTY_PRESETS],
            state="readonly", width=40,
        )
        property_combo.grid(row=0, column=1, columnspan=5, sticky="w", padx=(4, 0))
        property_combo.bind("<<ComboboxSelected>>", self._on_property_type_changed)

        # Altitude + radius + bearing fields
        ttk.Label(params_frame, text="Altitude (ft):").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.alt_var = tk.StringVar(value=DEFAULT_ALT_FT)
        ttk.Entry(params_frame, textvariable=self.alt_var, width=8).grid(row=1, column=1, sticky="w", padx=(4, 16), pady=(6, 0))

        ttk.Label(params_frame, text="Orbit Radius (ft):").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.radius_var = tk.StringVar(value=DEFAULT_ORBIT_RADIUS_FT)
        ttk.Entry(params_frame, textvariable=self.radius_var, width=8).grid(row=1, column=3, sticky="w", padx=(4, 16), pady=(6, 0))

        ttk.Label(params_frame, text="Front Bearing (°):").grid(row=1, column=4, sticky="w", pady=(6, 0))
        self.bearing_var = tk.StringVar()
        ttk.Entry(params_frame, textvariable=self.bearing_var, width=8).grid(row=1, column=5, sticky="w", padx=(4, 0), pady=(6, 0))

        # Nadir offset (fine-tune when the auto-detected centroid isn't on the house)
        ttk.Label(params_frame, text="Nadir Offset N/S (ft):").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.offset_ns_var = tk.StringVar(value="0")
        ttk.Entry(params_frame, textvariable=self.offset_ns_var, width=8).grid(row=2, column=1, sticky="w", padx=(4, 16), pady=(6, 0))

        ttk.Label(params_frame, text="Nadir Offset E/W (ft):").grid(row=2, column=2, sticky="w", pady=(6, 0))
        self.offset_ew_var = tk.StringVar(value="0")
        ttk.Entry(params_frame, textvariable=self.offset_ew_var, width=8).grid(row=2, column=3, sticky="w", padx=(4, 16), pady=(6, 0))

        ttk.Label(params_frame,
                  text="Altitude is above takeoff point. Property Type auto-fills alt = building height + "
                       f"{BEES360_CLEARANCE_FT} ft clearance (per Bees360 spec). Mission center auto-detects the "
                       "target building via Overture Maps (748K Hampton Roads buildings, pre-downloaded) filtered by "
                       "parcel polygon; falls back to OSM then parcel centroid. Use Nadir Offset (+ = north/east, "
                       "− = south/west) to nudge when auto-detection misses.",
                  foreground=TEXT_DIM, font=(FONT_FAMILY, 8), wraplength=560, justify="left").grid(
            row=3, column=0, columnspan=6, sticky="w", pady=(8, 0))

        # Output section
        out_frame = ttk.LabelFrame(body, text="Output", padding=10)
        out_frame.pack(fill="x", pady=(0, 8))

        ttk.Label(out_frame, text="Folder:").grid(row=0, column=0, sticky="w")
        self.output_dir_var = tk.StringVar(value=self._settings.get("mission_output_dir", DEFAULT_OUTPUT_DIR))
        ttk.Entry(out_frame, textvariable=self.output_dir_var).grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ttk.Button(out_frame, text="Browse...", command=self._browse_output_dir,
                   style="Secondary.TButton").grid(row=0, column=2, sticky="e")
        out_frame.grid_columnconfigure(1, weight=1)

        # Status / log area
        self.status_var = tk.StringVar(value="Ready.")
        status_label = tk.Label(body, textvariable=self.status_var,
                                anchor="w", justify="left", bg=BG_COLOR, fg=TEXT_DIM,
                                font=(FONT_FAMILY, 9), wraplength=560)
        status_label.pack(fill="x", pady=(4, 6))
        self._status_label = status_label

        # Buttons
        btn_frame = ttk.Frame(body)
        btn_frame.pack(fill="x", pady=(4, 0))

        self.open_folder_btn = ttk.Button(btn_frame, text="Open Folder",
                                          command=self._open_output_folder,
                                          style="Secondary.TButton")
        self.open_folder_btn.pack(side="left")
        self.open_folder_btn.state(["disabled"])

        self.preview_btn = ttk.Button(btn_frame, text="Preview Mission",
                                      command=self._on_preview,
                                      style="Secondary.TButton")
        self.preview_btn.pack(side="left", padx=(8, 0))
        self.preview_btn.state(["disabled"])

        ttk.Button(btn_frame, text="Close",
                   command=self.destroy,
                   style="Secondary.TButton").pack(side="right")

        self.generate_btn = ttk.Button(btn_frame, text="Generate KMZ",
                                       command=self._on_generate,
                                       style="Accent.TButton")
        self.generate_btn.pack(side="right", padx=(0, 8))

    def _center_on_parent(self, parent: tk.Misc):
        self.update_idletasks()
        try:
            px = parent.winfo_rootx()
            py = parent.winfo_rooty()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 3
            self.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    # ── Actions ──

    def _on_property_type_changed(self, _event=None):
        """Auto-populate altitude and radius fields when user picks a preset."""
        selected = self.property_type_var.get()
        for label, height_ft in PROPERTY_PRESETS:
            if label == selected and height_ft is not None:
                computed_alt = height_ft + BEES360_CLEARANCE_FT
                self.alt_var.set(str(computed_alt))
                self.radius_var.set(str(computed_alt))
                return
        # "Custom" selected — leave fields as-is for manual editing

    def _browse_output_dir(self):
        initial = self.output_dir_var.get() or DEFAULT_OUTPUT_DIR
        chosen = filedialog.askdirectory(
            parent=self, initialdir=initial, title="Pick KMZ output folder",
        )
        if chosen:
            self.output_dir_var.set(chosen)

    def _open_output_folder(self):
        if not self._last_output_path:
            return
        folder = self._last_output_path.parent
        try:
            os.startfile(str(folder))  # Windows-only; Sortie is Windows-targeted
        except OSError as e:
            messagebox.showerror("Open Folder", f"Could not open {folder}: {e}", parent=self)

    def _on_preview(self):
        """Render an interactive HTML map of the last generated KMZ and open it."""
        if not self._last_output_path or not self._last_output_path.exists():
            messagebox.showinfo("No Mission",
                                "Generate a KMZ first, then click Preview.", parent=self)
            return

        pipeline_dir = Path(self._settings.get("drone_pipeline_path", DEFAULT_DRONE_PIPELINE_PATH))
        script_path = pipeline_dir / "mission_preview.py"
        if not script_path.exists():
            messagebox.showerror(
                "mission_preview.py not found",
                f"Could not find:\n  {script_path}\n\n"
                "Update drone-pipeline (it ships with mission_preview.py).",
                parent=self,
            )
            return

        out_html = self._last_output_path.with_suffix(".preview.html")
        argv = [sys.executable, str(script_path), str(self._last_output_path),
                "--output", str(out_html), "--open"]

        self._set_status("Rendering preview...", color=TEXT_DIM)

        def worker():
            try:
                proc = subprocess.run(
                    argv, capture_output=True, text=True,
                    cwd=str(pipeline_dir), timeout=60,
                )
                self.after(0, self._on_preview_done, proc, out_html)
            except subprocess.TimeoutExpired:
                self.after(0, self._set_status, "Preview timed out (>60s)", RED)
            except Exception as e:
                self.after(0, self._set_status, f"Preview failed: {e}", RED)

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_done(self, proc: subprocess.CompletedProcess, out_html: Path):
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            self._set_status(f"Preview failed: {err}", color=RED)
            messagebox.showerror("Preview Failed", err, parent=self)
            return
        size_kb = out_html.stat().st_size / 1024 if out_html.exists() else 0
        self._set_status(
            f"Preview opened in browser - {out_html.name} ({size_kb:.1f} KB)\n"
            f"Email to clients as a flight-plan preview.",
            color=GREEN,
        )

    def _on_generate(self):
        if self._running:
            return

        # Validate inputs
        address = self.address_var.get().strip()
        lat_str = self.lat_var.get().strip()
        lon_str = self.lon_var.get().strip()
        if not address and not (lat_str and lon_str):
            messagebox.showwarning("Missing Input",
                                   "Provide an address, or both Lat and Lon.", parent=self)
            return

        try:
            alt_ft = float(self.alt_var.get())
            radius_ft = float(self.radius_var.get())
        except ValueError:
            messagebox.showerror("Invalid Number",
                                 "Altitude and Orbit Radius must be numbers (ft).", parent=self)
            return

        bearing_str = self.bearing_var.get().strip()
        bearing: float | None = None
        if bearing_str:
            try:
                bearing = float(bearing_str)
            except ValueError:
                messagebox.showerror("Invalid Number",
                                     "Front Bearing must be a number (degrees from N).", parent=self)
                return

        try:
            offset_ns = float(self.offset_ns_var.get() or "0")
            offset_ew = float(self.offset_ew_var.get() or "0")
        except ValueError:
            messagebox.showerror("Invalid Number",
                                 "Nadir Offset N/S and E/W must be numbers (ft).", parent=self)
            return

        output_dir = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR)
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror("Output Folder",
                                 f"Cannot create {output_dir}: {e}", parent=self)
            return

        # Resolve drone-pipeline location
        pipeline_dir = Path(self._settings.get("drone_pipeline_path", DEFAULT_DRONE_PIPELINE_PATH))
        script_path = pipeline_dir / "bees360_kmz.py"
        if not script_path.exists():
            messagebox.showerror(
                "drone-pipeline not found",
                f"Could not find:\n  {script_path}\n\n"
                "Install drone-pipeline or update its path in Sortie settings "
                "(key: drone_pipeline_path in sortie_settings.json).",
                parent=self,
            )
            return

        # Build CLI args
        argv = [sys.executable, str(script_path)]
        if address:
            argv.append(address)
        if lat_str and lon_str:
            argv += ["--lat", lat_str, "--lon", lon_str]
        if self.label_var.get().strip():
            argv += ["--label", self.label_var.get().strip()]
        argv += ["--alt-ft", str(alt_ft)]
        argv += ["--orbit-radius-ft", str(radius_ft)]
        if bearing is not None:
            argv += ["--front-bearing", str(bearing)]
        if offset_ns:
            argv += ["--offset-north-ft", str(offset_ns)]
        if offset_ew:
            argv += ["--offset-east-ft", str(offset_ew)]
        # Send the generated file to the chosen output dir under the auto-name.
        # We pass --output only if user chose a non-default folder so the script's
        # auto-naming kicks in; otherwise the script writes to drone-pipeline/kml/.
        # To keep the user's folder choice authoritative, we always set --output
        # to a folder-anchored name we compute here.
        from datetime import datetime
        if address:
            base = address.split(",")[0].strip().replace(" ", "_")
        else:
            base = (self.label_var.get().strip() or f"{lat_str}_{lon_str}").replace(" ", "_")
        # Strip non-filename-safe chars
        base = "".join(c for c in base if c.isalnum() or c == "_")[:60]
        date_str = datetime.now().strftime("%Y%m%d")
        out_path = output_dir / f"Bees360_{base}_{date_str}.kmz"
        argv += ["--output", str(out_path)]

        # Persist output dir choice to settings
        if self._save_cb:
            try:
                self._settings["mission_output_dir"] = str(output_dir)
                self._save_cb(self._settings)
            except Exception:
                pass

        self._set_running(True)
        self._set_status("Generating KMZ...", color=TEXT_DIM)
        self._last_output_path = None
        self.open_folder_btn.state(["disabled"])

        # Run in a thread so the UI stays responsive
        def worker():
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    cwd=str(pipeline_dir),
                    timeout=90,
                )
                self.after(0, self._on_complete, proc, out_path)
            except subprocess.TimeoutExpired:
                self.after(0, self._on_error, "Timed out (>90s)")
            except Exception as e:
                self.after(0, self._on_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_complete(self, proc: subprocess.CompletedProcess, expected_out: Path):
        self._set_running(False)
        if proc.returncode != 0:
            err = proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}"
            self._set_status(f"Failed: {err}", color=RED)
            messagebox.showerror("Generation Failed", err, parent=self)
            return

        # Parse the script's stdout JSON for the actual output path
        actual_path = expected_out
        try:
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            if payload.get("status") == "ok" and payload.get("output"):
                actual_path = Path(payload["output"])
        except (json.JSONDecodeError, IndexError, KeyError):
            pass

        self._last_output_path = actual_path
        size_kb = actual_path.stat().st_size / 1024 if actual_path.exists() else 0
        self._set_status(
            f"OK - {actual_path.name} ({size_kb:.1f} KB)\n"
            f"Click Preview Mission to validate the flight path, "
            f"or sideload via WaypointMapKMZInstaller and check DJI Fly's mission library.",
            color=GREEN,
        )
        self.open_folder_btn.state(["!disabled"])
        self.preview_btn.state(["!disabled"])

    def _on_error(self, msg: str):
        self._set_running(False)
        self._set_status(f"Failed: {msg}", color=RED)
        messagebox.showerror("Generation Failed", msg, parent=self)

    # ── Utilities ──

    def _set_running(self, running: bool):
        self._running = running
        new_state = ["disabled"] if running else ["!disabled"]
        self.generate_btn.state(new_state)

    def _set_status(self, text: str, color: str = TEXT_DIM):
        self.status_var.set(text)
        try:
            self._status_label.configure(fg=color)
        except tk.TclError:
            pass


if __name__ == "__main__":
    # Standalone smoke test
    root = tk.Tk()
    root.withdraw()
    settings = {}
    MissionPlannerDialog(root, settings)
    root.mainloop()
