#!/usr/bin/env python3
"""Desktop camera-capture and XY reconstruction workflow."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application.side_xy_calibration_gcode import prepare_calibration_copy
from application.integrity_tab import IntegrityTab

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    DND_FILES = None
    TkinterDnD = None


RUNS_ROOT = ROOT / "outputs" / "runs"
GCODE_EXTENSIONS = {".gcode", ".gco", ".gc"}


def export_xy(run_folder, destination):
    """Export a completed, calibrated bed-XY reconstruction without overwriting its source."""
    run_folder = Path(run_folder).resolve()
    destination = Path(destination).expanduser().resolve()
    metadata_path = run_folder / "metadata.json"
    source = run_folder / "reconstructed_path_only.gcode"
    if not metadata_path.is_file() or not source.is_file():
        raise ValueError("The camera run does not contain a completed reconstruction.")
    metadata = json.loads(metadata_path.read_text())
    if metadata.get("processing_status") != "complete":
        raise ValueError("The camera run did not finish reconstruction successfully.")
    if metadata.get("bed_xy_commands_available") is not True:
        raise ValueError("Calibrated bed XY is unavailable; no XY G-code was exported.")
    if destination == source.resolve() or run_folder in destination.parents:
        raise ValueError("Choose a destination outside the camera run folder.")
    if not destination.parent.is_dir():
        raise ValueError("Choose an existing destination folder.")
    if destination.suffix.lower() not in GCODE_EXTENSIONS:
        raise ValueError("Choose a .gcode, .gco, or .gc destination.")

    content = source.read_text()
    if not any(line.startswith(("G0 X", "G1 X", "G0 Y", "G1 Y")) for line in content.splitlines()):
        raise ValueError("The reconstruction contains no supported XY movement commands.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{destination.name}.", suffix=".tmp",
            dir=destination.parent, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with source.open("rb") as input_file:
                shutil.copyfileobj(input_file, handle)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def is_gcode(path):
    path = Path(path)
    return path.is_file() and path.suffix.lower() in GCODE_EXTENSIONS


class ReconstructionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Camera G-code Integrity")
        self.root.minsize(980, 640)
        self.process = None
        self.log = None
        self.calibration_process = None
        self.calibration_log = None
        self.calibration_output = None
        self.stop_file = None
        self.initial_runs = set()
        self.closing = False

        self.reference = tk.StringVar()
        self.input_source = tk.StringVar(value="0")
        self.calibration_source = tk.StringVar(value="0")
        self.view_mode = tk.StringVar(value="Side view")
        default_model = ROOT / "assets/models/printer_head/best.pt"
        if not default_model.is_file():
            default_model = ROOT / "outputs/training/legacy_dataset/runs/printer-head-3/weights/best.pt"
        self.model = tk.StringVar(value=str(default_model) if default_model.is_file() else "")
        self.side_calibration = tk.StringVar()
        self.output = tk.StringVar()
        self.runs_dir = tk.StringVar(value=str(RUNS_ROOT))
        self.status = tk.StringVar(value="Ready. Choose an output file, then start the camera.")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self):
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)
        self.integrity = IntegrityTab(notebook, self.root)
        notebook.add(self.integrity.frame, text="Integrity check (video vs G-code)")
        frame = ttk.Frame(notebook, padding=22)
        notebook.add(frame, text="Live capture / reconstruction")
        ttk.Label(frame, text="Camera XY G-code Reconstructor", font=("", 18, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Capture one camera view, then export its calibrated printer-space XY path.",
            wraplength=560,
        ).pack(anchor="w", pady=(5, 16))

        ttk.Label(frame, text="Camera view").pack(anchor="w")
        self.view_selector = ttk.Combobox(
            frame, textvariable=self.view_mode, values=("Side view", "Top view"), state="readonly",
        )
        self.view_selector.pack(fill="x", pady=(4, 12))
        self.view_selector.bind("<<ComboboxSelected>>", self._view_changed)

        self.side_controls = ttk.Frame(frame)
        self.side_controls.pack(fill="x", pady=(0, 12))
        ttk.Label(self.side_controls, text="Trained printer-head YOLO model").pack(anchor="w")
        model_row = ttk.Frame(self.side_controls)
        model_row.pack(fill="x", pady=(4, 8))
        ttk.Entry(model_row, textvariable=self.model).pack(side="left", fill="x", expand=True)
        ttk.Button(model_row, text="Choose model…", command=self._browse_model).pack(side="left", padx=(8, 0))
        ttk.Label(self.side_controls, text="Validated side-to-printer XY calibration").pack(anchor="w")
        calibration_row = ttk.Frame(self.side_controls)
        calibration_row.pack(fill="x", pady=(4, 0))
        ttk.Entry(calibration_row, textvariable=self.side_calibration).pack(side="left", fill="x", expand=True)
        ttk.Button(calibration_row, text="Choose calibration…", command=self._browse_side_calibration).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(calibration_row, text="Auto-calibrate…", command=self._run_side_calibration).pack(
            side="left", padx=(8, 0)
        )
        ttk.Button(
            self.side_controls,
            text="Prepare calibration G-code copy…",
            command=self._prepare_side_calibration_gcode,
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(self.side_controls, text="Calibration video or live camera").pack(anchor="w", pady=(10, 0))
        calibration_source_row = ttk.Frame(self.side_controls)
        calibration_source_row.pack(fill="x", pady=(4, 0))
        calibration_source_entry = ttk.Entry(calibration_source_row, textvariable=self.calibration_source)
        calibration_source_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            calibration_source_row,
            text="Choose calibration video…",
            command=self._browse_calibration_video,
        ).pack(side="left", padx=(8, 0))
        if DND_FILES is not None:
            calibration_source_entry.drop_target_register(DND_FILES)
            calibration_source_entry.dnd_bind("<<Drop>>", self._drop_calibration_video)
        ttk.Label(
            self.side_controls,
            text="For recorded video, start just before point A; select head and nozzle in the camera window, then press S.",
            wraplength=560,
        ).pack(anchor="w", pady=(4, 0))

        self.reference_label = ttk.Label(frame, text="Original G-code (optional reference; drop a file here)")
        self.reference_label.pack(anchor="w")
        reference_row = ttk.Frame(frame)
        reference_row.pack(fill="x", pady=(4, 12))
        reference_entry = ttk.Entry(reference_row, textvariable=self.reference)
        reference_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(reference_row, text="Browse…", command=self._browse_reference).pack(side="left", padx=(8, 0))
        if DND_FILES is not None:
            reference_entry.drop_target_register(DND_FILES)
            reference_entry.dnd_bind("<<Drop>>", self._drop_reference)

        ttk.Label(frame, text="Camera index or video file").pack(anchor="w")
        input_row = ttk.Frame(frame)
        input_row.pack(fill="x", pady=(4, 12))
        input_entry = ttk.Entry(input_row, textvariable=self.input_source)
        input_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(input_row, text="Choose video…", command=self._browse_video).pack(side="left", padx=(8, 0))
        if DND_FILES is not None:
            input_entry.drop_target_register(DND_FILES)
            input_entry.dnd_bind("<<Drop>>", self._drop_video)

        ttk.Label(frame, text="Save reconstructed XY G-code to").pack(anchor="w")
        output_row = ttk.Frame(frame)
        output_row.pack(fill="x", pady=(4, 12))
        ttk.Entry(output_row, textvariable=self.output).pack(side="left", fill="x", expand=True)
        ttk.Button(output_row, text="Choose output…", command=self._browse_output).pack(side="left", padx=(8, 0))

        ttk.Label(frame, text="Keep camera run records in").pack(anchor="w")
        runs_row = ttk.Frame(frame)
        runs_row.pack(fill="x", pady=(4, 16))
        ttk.Entry(runs_row, textvariable=self.runs_dir).pack(side="left", fill="x", expand=True)
        ttk.Button(runs_row, text="Choose folder…", command=self._browse_runs).pack(side="left", padx=(8, 0))

        self.action = ttk.Button(frame, text="Start camera capture", command=self._start_capture)
        self.action.pack(fill="x", ipady=7)
        ttk.Label(frame, textvariable=self.status, wraplength=560).pack(anchor="w", pady=(14, 8))
        self.capture_help = ttk.Label(
            frame,
            text="",
            wraplength=560,
        )
        self.capture_help.pack(anchor="w", pady=(0, 10))
        ttk.Label(
            frame,
            text=("Side view estimates depth from calibrated YOLO head width and lateral position from "
                  "the nozzle image position. Top view maps the nozzle through bed markers. "
                  "The exported file is an analysis path, not a printer-ready program."),
            wraplength=560,
        ).pack(anchor="w")
        if DND_FILES is None:
            ttk.Label(
                frame,
                text="Drag-and-drop needs tkinterdnd2; Browse buttons still work without it.",
                wraplength=560,
            ).pack(anchor="w", pady=(10, 0))
        self._view_changed()

    def _view_changed(self, event=None):
        if event is not None and event.widget is not self.view_selector:
            return
        if self.view_mode.get() == "Side view":
            self.side_controls.pack(fill="x", pady=(0, 12), before=self.reference_label)
            self.capture_help.configure(
                text=("Side camera: click the detected printer head, then SHIFT-click the nozzle tip. "
                      "V starts/stops; Space pauses; Q quits. Movement can start recording automatically.")
            )
        else:
            self.side_controls.pack_forget()
            self.capture_help.configure(
                text=("Top camera: press S and select the nozzle; V starts/stops; R resets calibration; "
                      "Q quits. Movement can start recording automatically.")
            )

    def _browse_reference(self):
        path = filedialog.askopenfilename(
            title="Choose original G-code",
            filetypes=[("G-code files", "*.gcode *.gco *.gc"), ("All files", "*")],
        )
        if path:
            self.reference.set(path)

    def _browse_video(self):
        path = filedialog.askopenfilename(
            title="Choose a camera video",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv"), ("All files", "*")],
        )
        if path:
            self.input_source.set(path)

    def _browse_calibration_video(self):
        path = filedialog.askopenfilename(
            title="Choose prerecorded side XY calibration video",
            filetypes=[("Video files", "*.mp4 *.mov *.avi *.mkv"), ("All files", "*")],
        )
        if path:
            self.calibration_source.set(path)

    def _browse_model(self):
        path = filedialog.askopenfilename(
            title="Choose trained printer-head YOLO weights",
            filetypes=[("PyTorch weights", "*.pt"), ("All files", "*")],
        )
        if path:
            self.model.set(path)

    def _browse_side_calibration(self):
        path = filedialog.askopenfilename(
            title="Choose validated side XY calibration",
            filetypes=[("Calibration JSON", "*.json"), ("All files", "*")],
        )
        if path:
            self.side_calibration.set(path)

    def _prepare_side_calibration_gcode(self):
        source = filedialog.askopenfilename(
            title="Choose the original G-code",
            filetypes=[("G-code files", "*.gcode *.gco *.gc"), ("All files", "*")],
        )
        if not source:
            return
        destination = filedialog.asksaveasfilename(
            title="Save a separate calibration-enabled G-code copy",
            defaultextension=".gcode",
            initialfile=f"{Path(source).stem}_side_xy_calibration.gcode",
            filetypes=[("G-code files", "*.gcode"), ("All files", "*")],
        )
        if not destination:
            return
        try:
            output = prepare_calibration_copy(source, destination)
        except (OSError, ValueError) as error:
            messagebox.showerror("Could not prepare calibration copy", str(error))
            return
        messagebox.showinfo(
            "Calibration copy ready",
            f"Created a separate file:\n{output}\n\n"
            "Inspect its moves and verify clearance before supervised use. "
            "The original G-code was not changed.",
        )

    def _run_side_calibration(self):
        if self.process is not None and self.process.poll() is None:
            messagebox.showerror(
                "Camera already in use",
                "Stop the current capture before starting side XY calibration.",
            )
            return
        if self.calibration_process is not None and self.calibration_process.poll() is None:
            messagebox.showerror("Calibration already running", "Finish or quit the current calibration window first.")
            return
        model = Path(self.model.get()).expanduser()
        if not model.is_file():
            messagebox.showerror("Choose a YOLO model", "Select reviewed printer_head YOLO weights first.")
            return
        source = self.calibration_source.get().strip()
        if not source or (not source.isdecimal() and not Path(source).is_file()):
            messagebox.showerror(
                "Choose calibration input",
                "Enter a camera index or choose an existing prerecorded calibration video.",
            )
            return
        output = filedialog.asksaveasfilename(
            title="Save fitted side XY calibration",
            defaultextension=".json",
            initialfile="side_xy_calibration.json",
            filetypes=[("Calibration JSON", "*.json")],
        )
        if not output:
            return
        if Path(output).expanduser().exists() or Path(output).expanduser().with_suffix(".csv").exists():
            messagebox.showerror(
                "Calibration output exists",
                "Choose a new JSON path; calibration outputs are not overwritten.",
            )
            return
        if not messagebox.askyesno(
            "Start side XY calibration",
            "Start this capture window, click the detected printer-head box, then SHIFT-click the nozzle tip. "
            "The separate calibration input field will be used. Choose a prerecorded calibration video "
            "or a live camera showing the calibration run. Press S as point A begins. "
            "The collector will sample the seven stable stops and report held-out error. "
            "If running the printer live, supervise it and verify all moves before running.",
        ):
            return
        command = [
            sys.executable,
            str(ROOT / "application/collect_side_xy_calibration.py"),
            "--input", source,
            "--model", str(model.resolve()),
            "--output", str(Path(output).expanduser().resolve()),
        ]
        self.calibration_output = Path(output).expanduser().resolve()
        self.calibration_log = tempfile.TemporaryFile(mode="w+b")
        try:
            self.calibration_process = subprocess.Popen(
                command, cwd=ROOT, stdout=self.calibration_log, stderr=subprocess.STDOUT,
            )
        except OSError as error:
            self.calibration_log.close()
            self.calibration_log = None
            self.calibration_output = None
            messagebox.showerror("Could not start calibration", str(error))
            return
        self.status.set("Side XY calibration is running in its camera window.")
        self.root.after(250, self._poll_side_calibration)

    def _poll_side_calibration(self):
        if self.calibration_process is None:
            return
        if self.calibration_process.poll() is None:
            self.root.after(250, self._poll_side_calibration)
            return
        exit_code = self.calibration_process.returncode
        output = self.calibration_output
        log_text = ""
        if self.calibration_log is not None:
            self.calibration_log.seek(0)
            log_text = self.calibration_log.read().decode("utf-8", errors="replace")
            self.calibration_log.close()
        self.calibration_process = None
        self.calibration_log = None
        self.calibration_output = None
        if exit_code != 0:
            self.status.set("Side XY calibration did not complete.")
            messagebox.showerror("Side XY calibration failed", log_text[-3000:] or "Check the camera and model.")
            return
        if output is None or not output.is_file():
            self.status.set("Side XY calibration ended without a calibration file.")
            messagebox.showerror("Calibration output missing", log_text[-3000:] or "No calibration JSON was created.")
            return
        self.side_calibration.set(str(output))
        self.status.set(f"Side XY calibration saved to {output}")
        messagebox.showinfo("Calibration complete", f"{log_text[-2000:]}\n\nCalibration saved:\n{output}")

    def _browse_output(self):
        path = filedialog.asksaveasfilename(
            title="Save reconstructed XY G-code",
            defaultextension=".gcode",
            filetypes=[("G-code files", "*.gcode"), ("All files", "*")],
        )
        if path:
            self.output.set(path)

    def _browse_runs(self):
        path = filedialog.askdirectory(title="Choose camera run folder")
        if path:
            self.runs_dir.set(path)

    def _drop_reference(self, event):
        paths = self.root.tk.splitlist(event.data)
        if len(paths) != 1 or not is_gcode(paths[0]):
            messagebox.showerror("Invalid G-code file", "Drop one existing .gcode, .gco, or .gc file.")
            return "break"
        self.reference.set(str(Path(paths[0]).resolve()))
        return "break"

    def _drop_video(self, event):
        paths = self.root.tk.splitlist(event.data)
        if len(paths) != 1 or not Path(paths[0]).is_file():
            messagebox.showerror("Invalid video", "Drop one existing video file.")
            return "break"
        self.input_source.set(str(Path(paths[0]).resolve()))
        return "break"

    def _drop_calibration_video(self, event):
        paths = self.root.tk.splitlist(event.data)
        if len(paths) != 1 or not Path(paths[0]).is_file():
            messagebox.showerror("Invalid calibration video", "Drop one existing calibration video file.")
            return "break"
        self.calibration_source.set(str(Path(paths[0]).resolve()))
        return "break"

    def _start_capture(self):
        if self.calibration_process is not None and self.calibration_process.poll() is None:
            messagebox.showerror("Calibration in progress", "Finish side XY calibration before starting capture.")
            return
        output = Path(self.output.get()).expanduser()
        if not output.name:
            messagebox.showerror("Choose an output", "Choose where the reconstructed G-code should be saved.")
            return
        if output.suffix.lower() not in GCODE_EXTENSIONS:
            messagebox.showerror("Invalid output", "The output filename must end in .gcode, .gco, or .gc.")
            return
        if not output.parent.is_dir():
            messagebox.showerror("Invalid output folder", "Choose an existing output folder.")
            return
        reference = self.reference.get().strip()
        if reference and not is_gcode(reference):
            messagebox.showerror("Invalid reference", "Choose or drop an existing G-code file.")
            return
        if reference and output.resolve() == Path(reference).resolve():
            messagebox.showerror("Invalid output", "The output path cannot overwrite the original G-code.")
            return
        if output.exists() and not messagebox.askyesno(
            "Replace existing file?", f"Replace this file with the reconstructed XY path?\n\n{output}"
        ):
            return
        source = self.input_source.get().strip()
        if not source:
            messagebox.showerror("Choose a camera", "Enter a camera index (for example 0) or choose a video.")
            return
        runs_root = Path(self.runs_dir.get()).expanduser()
        try:
            runs_root.mkdir(parents=True, exist_ok=True)
            self.initial_runs = {path.name for path in runs_root.iterdir() if path.is_dir()}
            handle, stop_path = tempfile.mkstemp(prefix="gcode-reconstructor-stop-")
            os.close(handle)
            self.stop_file = Path(stop_path)
            self.stop_file.unlink()
        except OSError as error:
            self._remove_stop_file()
            messagebox.showerror("Cannot prepare camera run", str(error))
            return
        if self.view_mode.get() == "Side view":
            model_path = Path(self.model.get()).expanduser()
            calibration_path = Path(self.side_calibration.get()).expanduser()
            if not model_path.is_file():
                self._remove_stop_file()
                messagebox.showerror("Choose a YOLO model", "Select trained printer_head YOLO weights (.pt) for side view.")
                return
            if not calibration_path.is_file():
                self._remove_stop_file()
                messagebox.showerror(
                    "Choose side XY calibration",
                    "Select a validated side-to-printer XY calibration JSON. "
                    "See the capture README for creating it from known bed positions.",
                )
                return
            command = [
                sys.executable, str(ROOT / "cameras/side/printer_xy_tracker.py"),
                "--input", source, "--runs-dir", str(runs_root),
                "--model", str(model_path.resolve()),
                "--xy-calibration", str(calibration_path.resolve()),
                "--stop-file", str(self.stop_file),
            ]
        else:
            command = [
                sys.executable, str(ROOT / "application/experiment.py"), "capture",
                "--view", "up_view", "--input", source,
                "--runs-dir", str(runs_root), "--no-reference-prompt",
                "--stop-file", str(self.stop_file),
            ]
        if reference:
            command.extend(["--reference", str(Path(reference).resolve())])
        self.log = tempfile.TemporaryFile(mode="w+b")
        try:
            self.process = subprocess.Popen(
                command, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT,
            )
        except OSError as error:
            self.log.close()
            self.log = None
            self._remove_stop_file()
            messagebox.showerror("Could not start camera", str(error))
            return
        self.action.configure(text="Stop camera & Recode XY", command=self._stop_capture)
        self.status.set("Camera is running. Select the nozzle in its window; stop here to build the XY output.")
        self.root.after(250, self._poll_capture)

    def _stop_capture(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            if self.stop_file is not None:
                self.stop_file.touch()
        except OSError as error:
            messagebox.showerror("Cannot stop camera", str(error))
            return
        self.action.configure(state="disabled")
        self.status.set("Stopping camera and reconstructing the captured XY path…")

    def _poll_capture(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.root.after(250, self._poll_capture)
            return

        exit_code = self.process.returncode
        log_text = ""
        if self.log is not None:
            self.log.seek(0)
            log_text = self.log.read().decode("utf-8", errors="replace")
            self.log.close()
            self.log = None
        self.process = None
        self._remove_stop_file()

        if exit_code != 0:
            self._reset_action()
            self.status.set("Camera capture failed.")
            messagebox.showerror("Camera capture failed", log_text[-3000:] or "Check camera permissions and install capture/requirements.txt.")
            self._finish_close()
            return

        runs_root = Path(self.runs_dir.get()).expanduser()
        try:
            new_runs = [
                path for path in runs_root.iterdir()
                if path.is_dir() and path.name not in self.initial_runs and (path / ".experiment.json").is_file()
            ]
        except OSError as error:
            self._reset_action()
            self.status.set("Camera run ended, but its output folder could not be read.")
            messagebox.showerror("Cannot read camera run", str(error))
            self._finish_close()
            return
        if not new_runs:
            self._reset_action()
            self.status.set("No recording was created. Move the nozzle or press V in the camera window to record.")
            self._finish_close()
            return
        run_folder = max(new_runs, key=lambda path: path.name)
        try:
            destination = export_xy(run_folder, self.output.get())
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._reset_action()
            self.status.set(f"Camera run saved at {run_folder}; XY export was not available.")
            messagebox.showerror("XY G-code was not exported", f"{error}\n\nCamera run retained at:\n{run_folder}")
            self._finish_close()
            return

        self._reset_action()
        self.status.set(f"XY analysis saved to {destination}")
        messagebox.showinfo("Reconstruction complete", f"XY analysis saved to:\n{destination}\n\nCamera run retained at:\n{run_folder}")
        self._finish_close()

    def _reset_action(self):
        self.action.configure(text="Start camera capture", command=self._start_capture, state="normal")

    def _remove_stop_file(self):
        if self.stop_file is None:
            return
        try:
            self.stop_file.unlink(missing_ok=True)
        except OSError as error:
            messagebox.showwarning("Temporary-file cleanup failed", str(error))
        finally:
            self.stop_file = None

    def _close(self):
        self.integrity.stop()
        if self.process is not None and self.process.poll() is None:
            self.closing = True
            self._stop_capture()
            return
        if self.calibration_process is not None:
            if self.calibration_process.poll() is None:
                self.calibration_process.terminate()
            self.calibration_process = None
            if self.calibration_log is not None:
                self.calibration_log.close()
                self.calibration_log = None
        self.root.destroy()

    def _finish_close(self):
        if self.closing:
            self.root.destroy()


def main():
    root = TkinterDnD.Tk() if TkinterDnD is not None else tk.Tk()
    ReconstructionApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
