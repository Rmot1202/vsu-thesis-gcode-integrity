# Capture and analysis workflow

The desktop application is the recommended entry point. Launch
`python application/gui.py` from `capture/`, drop or select the original G-code
and video, choose side or top view, and select an output path when exporting.
The terminal-based CLI remains available but is optional.

## Permanent records

Runs are written under `capture/outputs/runs/`. Once created, a run is a
permanent experiment record. Capture/import folders receive `.experiment.json`
protection before recording begins. Interrupted recordings and processing
failures remain saved.

The CLI's startup cleanup examines only immediate run-root subfolders. It may
remove a folder only when `.test_run.json` explicitly contains
`{"kind": "print_capture_test_run", "delete_on_startup": true}`. Permanent
records, unmarked folders, symlinks, and symlinked markers are skipped.
Deletions are logged to `startup_cleanup.jsonl`.

## Capture and processing

Side and top camera modules record videos and trajectory CSVs. The side mode
requires a trained head detector and validated side-to-bed calibration to
produce printer-space XY. The top mode uses bed fiducials for calibrated
coordinates. The optional HSV top tracker emits warped pixel coordinates and
must not be treated as metric data without suitable calibration.

An original G-code file is comparison input, not a substitute for missing
camera evidence. Processing selects the justified coordinate frame, segments
gaps and motion, optionally applies supplied state/layer evidence, then writes
plots, summaries, and analysis G-code. Read `comparison_summary.txt` and the
reconstruction summary to understand calibration status and limitations.

G-code output is strictly for analysis, not printing. It does not reconstruct
unsupported extrusion, printer settings, or safe travel behavior.

## Reprocessing and retention

Reprocessing updates only files listed in the reconstruction manifest.
Previous analysis is retained in `analysis_history/<timestamp>/`; replaced
reference/evidence inputs are copied to `input_history/`. Unknown run files are
not silently overwritten.

The low-level reconstruction command remains available for isolated
analysis/testing; do not point it at a mixed capture folder. The GUI and
`application/experiment.py` CLI are preferred for permanent integrated runs.
See [run commands](RUN_COMMANDS.md) and [outputs](OUTPUTS.md).
