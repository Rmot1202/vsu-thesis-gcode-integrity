# Experiment outputs

Each run lives in `capture/runs/{view}_{date}_{time}/`. `view` is side_view,
up_view, or up_auto_view. `metadata.json` and G-code headers also identify it.

| File | Purpose |
| --- | --- |
| `raw_video.mp4` | Original unannotated frames; no raw video for CSV-only imports |
| `annotated_video.mp4` | Tracking visualization and recording status |
| `bed_grid.mp4` | Additional top-down video for the printed-fiducial upward tracker |
| `trajectory.csv` | Camera-module measurements, validity flags and source coordinates |
| `metadata.json` | View, settings, timestamps, capture/processing/comparison status |
| `.experiment.json` | Permanent-record protection marker; never mark this run disposable |
| `calibration_snapshot.json` | Additional upward-camera calibration snapshot, where available |
| `original_reference.gcode` | Exact supplied reference; absent until provided |
| `reference_path_only.gcode` | Reference geometric path, or unavailable placeholder |
| `reconstructed_path_only.gcode` | Supported observation path; comments-only without bed XY |
| `reconstructed_layered_path_only.gcode` | Written when multiple observed layer labels exist |
| `reference_path.png` | Source geometry |
| `reconstructed_path.png` | Observed geometry in its native coordinate frame |
| `overlay_comparison.png` | Common-frame overlay, or explicitly separate panels |
| `comparison_summary.txt` | Comparison availability, diagnostic metrics and limitations |
| `reconstruction_summary.txt` / `.json` | Sources, hashes, assumptions, uncertainty and settings |
| `observations_raw.csv` / `observations_cleaned.csv` | Input audit and optional cleanup results |
| `reconstructed_points.csv` / `reference_points.csv` | Exported point-level provenance |
| `layer_*.png` / `layer_overlay_*.png` | Optional layer-specific images |
| `README.md`, `WORKFLOW.md`, `OUTPUTS.md` | Documentation retained with the record |

Schemas differ by view, with explicit labels: side-camera image_u/image_v are
pixels, side_horizontal/vertical_mm are side-plane coordinates, and upward
nozzle_x/y_bed_mm are calibrated bed coordinates. HSV bed_x/bed_y are warped
pixels. Do not infer units from the filename alone.

G-code outputs are **ANALYSIS ONLY / NOT FOR PRINTING**. They omit unsupported
E, F, temperature, fan, acceleration, retraction and startup/end sequences.
Unknown layer/Z values remain unspecified. A segment-start G0 is an analysis
anchor, not verified safe printer travel. Side-view pixels do not become printer
XY just because an original G-code was provided.

The HSV tracker's optional legacy `--gcode` flag produces a separate
`configured_xy_legacy.gcode` using configured dimensions/feedrate. It is not the
conservative reconstruction result; omit that flag for this experiment workflow.

Incomplete captures remain saved. If processing fails, metadata records the
error and any previous analysis may remain; do not mistake it for a successful
new result. Reprocessing retains prior versions under analysis_history/.
