# Outputs

Each permanent capture or import is stored in
`capture/outputs/runs/{view}_{date}_{time}/`. The GUI's **Recode** action
exports a selected G-code result to the path chosen by the user. CLI runs use
the default run directory unless `--runs-dir` is supplied.

| File | Purpose |
| --- | --- |
| `raw_video.mp4` | Original frames; absent for CSV-only imports |
| `annotated_video.mp4` | Tracking visualization and recording status |
| `bed_grid.mp4` | Additional top-down view from the fiducial tracker |
| `trajectory.csv` | Camera measurements, validity flags, and coordinate provenance |
| `metadata.json` | View, settings, timestamps, capture and processing status |
| `.experiment.json` | Permanent-record protection marker |
| `calibration_snapshot.json` | Top-camera calibration snapshot, when available |
| `original_reference.gcode` | Supplied reference G-code, when available |
| `reference_path_only.gcode` | Reference geometry or unavailable placeholder |
| `reconstructed_path_only.gcode` | Supported observed path; may contain comments only |
| `reconstructed_layered_path_only.gcode` | Optional output when multiple layer labels exist |
| `reference_path.png`, `reconstructed_path.png` | Reference and observed geometry |
| `overlay_comparison.png` | Shared-frame overlay or separate panels |
| `comparison_summary.txt` | Comparison status, metrics, and limitations |
| `reconstruction_summary.txt` / `.json` | Sources, hashes, assumptions, and settings |
| `observations_raw.csv`, `observations_cleaned.csv` | Input audit and optional cleanup |
| `reconstructed_points.csv`, `reference_points.csv` | Point-level export and provenance |
| `layer_*.png`, `layer_overlay_*.png` | Optional per-layer plots |
| `RUN_INFO.txt` | Pointer to the centralized documentation |

Generated training datasets and model runs are stored under
`capture/outputs/training/`. Post-analysis outputs are under
`capture/outputs/post_analysis/`. Project inputs such as source videos and
reference G-code are kept separately in `capture/inputs/`.

Coordinate units are explicit in trajectory column names and metadata. Side
image pixels, side-plane millimeters, warped pixels, and calibrated printer
bed millimeters are different coordinate spaces; do not infer units from a
filename. Side-to-bed XY is only valid when the calibrated mapping is applied.

**All reconstructed G-code is analysis-only and not safe or ready to print.**
It omits unsupported extrusion, feed, temperature, fan, acceleration,
retraction, and startup/end sequences. Unknown layer/Z values remain
unspecified. A segment-start G0 is an analysis anchor, not verified safe
printer travel. The supplied original G-code is used as a comparison reference;
it does not fill missing camera measurements.

Incomplete captures remain saved. If processing fails, metadata records the
error and previous analyses may remain. Reprocessing retains replaced analysis
under `analysis_history/` and replaced inputs under `input_history/`.
