"""GUI tab for the camera-vs-G-code integrity check.

Layering: this file only builds widgets and starts ``integrity_check.py`` in a
subprocess (so the window never freezes while YOLO tracks a long video).
All the analysis lives in ``backend/integrity``.
"""
import json
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "application" / "integrity_check.py"
INTEGRITY_OUT = ROOT / "outputs" / "integrity"
DEFAULT_CALIBRATION = INTEGRITY_OUT / "calibration.json"
DEFAULT_MODEL = ROOT / "assets" / "models" / "printer_head" / "best.pt"
VIDEO_TYPES = [("Video", "*.mov *.MOV *.mp4 *.m4v *.avi *.mkv"), ("All files", "*")]
GCODE_TYPES = [("G-code", "*.gcode *.gco *.gc"), ("All files", "*")]


def gcode_name(gcode):
    """Readable name: 'circle' for inputs/gcode/circle/model.gcode or builds/circle/3D/model.gcode."""
    gcode = Path(gcode)
    if gcode.stem != "model":
        return gcode.stem
    if gcode.parent.name == "3D":  # Cura/UFP layout
        return gcode.parent.parent.name
    return gcode.parent.name


def default_output_dir(video, gcode):
    """outputs/integrity/<video>_<gcode name>."""
    video, gname = Path(video), gcode_name(gcode)
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in f"{video.stem}_{gname}")
    return INTEGRITY_OUT / safe


def _normalise(name):
    # macOS screen recordings use a narrow no-break space before "PM"
    return " ".join(str(name).replace("\u202f", " ").replace("\u00a0", " ").split()).lower()


def cached_track(video, tracks_dir=None):
    """Re-use a saved head track for this video if there is one.

    Looks for ``tracks/<video stem>.csv`` and for the video's file name in
    ``tracks/index.json`` (maps original recording names to track files).
    """
    tracks_dir = Path(tracks_dir or INTEGRITY_OUT / "tracks")
    candidate = tracks_dir / f"{Path(video).stem}.csv"
    if candidate.is_file():
        return candidate
    index = tracks_dir / "index.json"
    if index.is_file():
        names = {_normalise(k): v for k, v in json.loads(index.read_text()).items()}
        hit = names.get(_normalise(Path(video).name))
        if hit and (tracks_dir / hit).is_file():
            return tracks_dir / hit
    return None


def remember_track(video, track_csv, tracks_dir=None):
    """Save a freshly computed track so the next check of this video is instant."""
    import shutil

    tracks_dir = Path(tracks_dir or INTEGRITY_OUT / "tracks")
    tracks_dir.mkdir(parents=True, exist_ok=True)
    target = tracks_dir / f"{Path(video).stem}.csv"
    if Path(track_csv).is_file() and not target.exists():
        shutil.copyfile(track_csv, target)
    return target


def build_compare_command(video, gcode, calibration, output_dir, model=DEFAULT_MODEL, track=None,
                          tolerance=None, device="cpu", python=sys.executable):
    """Command line for one check; tracks the video first unless a track CSV is given."""
    if track:
        cmd = [python, str(CLI), "compare", "--track", str(track), "--video", str(video)]
    else:
        cmd = [python, str(CLI), "run", "--video", str(video), "--model", str(model), "--device", device]
    cmd += ["--gcode", str(gcode), "--calibration", str(calibration), "--output-dir", str(output_dir),
            "--label", f"{Path(video).name} vs {gcode_name(gcode)} G-code"]
    if tolerance:
        cmd += ["--tolerance", str(tolerance)]
    return cmd


def build_calibrate_commands(video, gcode, calibration_out, model=DEFAULT_MODEL, track=None,
                             device="cpu", python=sys.executable):
    """Track (if needed) then calibrate from a print whose G-code is trusted."""
    cmds = []
    if not track:
        track = INTEGRITY_OUT / "tracks" / f"{Path(video).stem}.csv"
        cmds.append([python, str(CLI), "track", "--video", str(video), "--output", str(track),
                     "--model", str(model), "--device", device])
    cmds.append([python, str(CLI), "calibrate", "--track", str(track), "--gcode", str(gcode),
                 "--output", str(calibration_out)])
    return cmds


class IntegrityTab:
    def __init__(self, parent, root):
        self.root = root
        self.frame = ttk.Frame(parent, padding=18)
        self.process = None
        self.queue = []
        self.log_path = None
        self.result_dir = None
        self._photo = None

        self.video = tk.StringVar()
        self.gcode = tk.StringVar()
        self.calibration = tk.StringVar(value=str(DEFAULT_CALIBRATION) if DEFAULT_CALIBRATION.is_file() else "")
        self.model = tk.StringVar(value=str(DEFAULT_MODEL) if DEFAULT_MODEL.is_file() else "")
        self.output = tk.StringVar()
        self.device = tk.StringVar(value="cpu")
        self.tolerance = tk.StringVar()
        self.status = tk.StringVar(value="Choose a print video and the G-code it was supposed to print.")
        self.verdict = tk.StringVar()
        self._build()

    # ------------------------------------------------------------------ layout
    def _row(self, label, var, command, button="Browse..."):
        ttk.Label(self.form, text=label).pack(anchor="w")
        row = ttk.Frame(self.form)
        row.pack(fill="x", pady=(3, 9))
        ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text=button, command=command).pack(side="left", padx=(8, 0))

    def _build(self):
        ttk.Label(self.frame, text="Integrity check", font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(self.frame, wraplength=900, text=(
            "Tracks the printer head in a recorded print, converts it to printer X/Y (mm) with the camera "
            "calibration, and compares it with the G-code (first layer)."
        )).pack(anchor="w", pady=(4, 12))
        body = ttk.Frame(self.frame)
        body.pack(fill="both", expand=True)
        self.form = ttk.Frame(body, width=430)
        self.form.pack(side="left", fill="y")
        self._row("Print video", self.video, self._browse_video)
        self._row("G-code that should have been printed", self.gcode, self._browse_gcode)
        self._row("Camera calibration (.json)", self.calibration, self._browse_calibration)
        self._row("Printer-head model (.pt)", self.model, self._browse_model)
        self._row("Save results to (folder, optional)", self.output, self._browse_output)
        opts = ttk.Frame(self.form)
        opts.pack(fill="x", pady=(0, 10))
        ttk.Label(opts, text="Device").pack(side="left")
        ttk.Combobox(opts, textvariable=self.device, values=("cpu", "mps", "0"), width=6,
                     state="readonly").pack(side="left", padx=(6, 16))
        ttk.Label(opts, text="Tolerance mm (blank = from calibration)").pack(side="left")
        ttk.Entry(opts, textvariable=self.tolerance, width=6).pack(side="left", padx=(6, 0))

        self.run_button = ttk.Button(self.form, text="Run integrity check", command=self.run_check)
        self.run_button.pack(fill="x", ipady=6)
        self.cal_button = ttk.Button(self.form, text="Calibrate camera from this video + G-code...",
                                     command=self.run_calibration)
        self.cal_button.pack(fill="x", pady=(8, 0))
        buttons = ttk.Frame(self.form)
        buttons.pack(fill="x", pady=(8, 0))
        self.cancel_button = ttk.Button(buttons, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.pack(side="left")
        ttk.Button(buttons, text="Open results folder", command=self.open_results).pack(side="left", padx=8)
        self.progress = ttk.Progressbar(self.form, mode="indeterminate")
        self.progress.pack(fill="x", pady=(10, 4))
        ttk.Label(self.form, textvariable=self.status, wraplength=420).pack(anchor="w")
        self.verdict_label = ttk.Label(self.form, textvariable=self.verdict, font=("", 13, "bold"),
                                       wraplength=420)
        self.verdict_label.pack(anchor="w", pady=(8, 0))

        self.preview = ttk.Label(body, text="The side-by-side figure appears here.", anchor="center")
        self.preview.pack(side="left", fill="both", expand=True, padx=(16, 0))

    # ------------------------------------------------------------------ browse
    def _browse_video(self):
        path = filedialog.askopenfilename(title="Print video", filetypes=VIDEO_TYPES)
        if path:
            self.video.set(path)

    def _browse_gcode(self):
        path = filedialog.askopenfilename(title="Expected G-code", filetypes=GCODE_TYPES,
                                          initialdir=ROOT / "inputs" / "gcode")
        if path:
            self.gcode.set(path)

    def _browse_calibration(self):
        path = filedialog.askopenfilename(title="Camera calibration", filetypes=[("JSON", "*.json")],
                                          initialdir=INTEGRITY_OUT)
        if path:
            self.calibration.set(path)

    def _browse_model(self):
        path = filedialog.askopenfilename(title="Printer-head model", filetypes=[("YOLO weights", "*.pt")])
        if path:
            self.model.set(path)

    def _browse_output(self):
        path = filedialog.askdirectory(title="Results folder", initialdir=INTEGRITY_OUT)
        if path:
            self.output.set(path)

    # ------------------------------------------------------------------ actions
    def _check_inputs(self, need_calibration=True):
        video, gcode = Path(self.video.get()), Path(self.gcode.get())
        if not video.is_file():
            raise ValueError("Choose the print video.")
        if not gcode.is_file():
            raise ValueError("Choose the G-code file.")
        if need_calibration and not Path(self.calibration.get()).is_file():
            raise ValueError("Choose a camera calibration, or create one with 'Calibrate camera...'.")
        if not cached_track(video) and not Path(self.model.get()).is_file():
            raise ValueError("Choose the printer-head model (.pt).")
        return video, gcode

    def run_check(self):
        try:
            video, gcode = self._check_inputs()
            tol = float(self.tolerance.get()) if self.tolerance.get().strip() else None
        except ValueError as error:
            messagebox.showerror("Integrity check", str(error))
            return
        out = Path(self.output.get()) if self.output.get().strip() else default_output_dir(video, gcode)
        track = cached_track(video)
        self._video_for_cache = None if track else video
        self.result_dir = out
        cmd = build_compare_command(video, gcode, self.calibration.get(), out, self.model.get(), track,
                                    tol, self.device.get())
        note = "using the saved head track" if track else "tracking the head first (can take several minutes)"
        self._start([cmd], f"Checking {video.name} against {gcode.name}, {note}...", self._show_result)

    def run_calibration(self):
        try:
            video, gcode = self._check_inputs(need_calibration=False)
        except ValueError as error:
            messagebox.showerror("Calibrate camera", str(error))
            return
        if not messagebox.askokcancel("Calibrate camera", (
                "Use this video as the calibration print?\n\nOnly do this with a print whose G-code you trust "
                "(for example the rectangle). The camera must not move afterwards.")):
            return
        target = filedialog.asksaveasfilename(title="Save calibration as", defaultextension=".json",
                                              initialdir=INTEGRITY_OUT, initialfile="calibration.json")
        if not target:
            return
        self._pending_calibration = target
        cmds = build_calibrate_commands(video, gcode, target, self.model.get(), cached_track(video),
                                        self.device.get())
        self._start(cmds, f"Calibrating from {video.name}...", self._calibration_done)

    def _start(self, cmds, message, on_success):
        if self.process is not None:
            return
        INTEGRITY_OUT.mkdir(parents=True, exist_ok=True)
        self.queue = list(cmds)
        self.on_success = on_success
        self.log_path = INTEGRITY_OUT / "gui_last_run.log"
        self.log_path.write_text("")
        self.status.set(message)
        self.verdict.set("")
        for b in (self.run_button, self.cal_button):
            b.config(state="disabled")
        self.cancel_button.config(state="normal")
        self.progress.start(12)
        self._next()

    def _next(self):
        cmd = self.queue.pop(0)
        with self.log_path.open("a") as log:
            log.write("$ " + " ".join(cmd) + "\n")
        self._log = self.log_path.open("a")
        self.process = subprocess.Popen(cmd, cwd=ROOT, stdout=self._log, stderr=subprocess.STDOUT)
        self.root.after(500, self._poll)

    def _poll(self):
        if self.process is None:
            return
        code = self.process.poll()
        if code is None:
            tail = self._last_log_line()
            if tail:
                self.status.set(tail[:160])
            self.root.after(500, self._poll)
            return
        self._log.close()
        self.process = None
        if code != 0:
            self._finish()
            self.status.set(f"Failed: {self._last_log_line() or 'see ' + str(self.log_path)}")
            return
        if self.queue:
            self._next()
            return
        self._finish()
        self.on_success()

    def _last_log_line(self):
        try:
            lines = [ln.strip() for ln in self.log_path.read_text(errors="replace").splitlines() if ln.strip()]
            return lines[-1] if lines else ""
        except OSError:
            return ""

    def _finish(self):
        self.progress.stop()
        for b in (self.run_button, self.cal_button):
            b.config(state="normal")
        self.cancel_button.config(state="disabled")

    def cancel(self):
        self.queue = []
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        self.status.set("Cancelled.")

    def stop(self):
        self.cancel()

    def _calibration_done(self):
        cal = json.loads(Path(self._pending_calibration).read_text())
        self.calibration.set(self._pending_calibration)
        self.status.set(f"Calibration saved: {self._pending_calibration}")
        self.verdict.set(f"Camera calibrated: held-out error {cal['holdout_rmse_mm']:.1f} mm "
                         f"(fit {cal['fit_rmse_mm']:.1f} mm)")

    def _show_result(self):
        if getattr(self, "_video_for_cache", None):
            remember_track(self._video_for_cache, self.result_dir / "track.csv")
        m = json.loads((self.result_dir / "comparison.json").read_text())
        ok = m["verdict"].startswith("CONSISTENT")
        self.verdict.set(
            f"{'PASS' if ok else 'FAIL'}: {m['verdict']}\n"
            f"95% of points within {m['path_deviation_p95_mm']:.1f} mm (limit {m['tolerance_mm']:g} mm), "
            f"{100 * m['reference_covered_fraction']:.0f}% of the path covered, "
            f"print starts at {m['print_start_video_s']:.1f} s in the video")
        self.verdict_label.config(foreground="#1e7d32" if ok else "#b71c1c")
        self.status.set(f"Results saved in {self.result_dir}")
        self._show_image(self.result_dir / "side_by_side.png")

    def _show_image(self, path):
        try:
            from PIL import Image, ImageTk
        except ImportError:
            self.preview.config(text=f"Figure saved: {path}")
            return
        img = Image.open(path)
        width = max(500, self.preview.winfo_width() or 900)
        img.thumbnail((width, 2000))
        self._photo = ImageTk.PhotoImage(img)
        self.preview.config(image=self._photo, text="")

    def open_results(self):
        folder = self.result_dir if self.result_dir and Path(self.result_dir).exists() else INTEGRITY_OUT
        opener = {"darwin": "open", "win32": "explorer"}.get(sys.platform, "xdg-open")
        subprocess.Popen([opener, str(folder)])
