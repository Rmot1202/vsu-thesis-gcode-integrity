# Reconstruction and G-code export

The GUI and `application/experiment.py` CLI use the shared reconstruction
pipeline in `backend/reconstruction/`. A source reference G-code is used for
comparison only; it cannot supply camera positions that were not observed.

The pipeline preserves coordinate provenance and does not silently treat
pixels, side-plane measurements, or unknown camera coordinates as printer-bed
millimeters. Top-view fiducial tracking can provide calibrated bed coordinates.
Side-view XY is accepted only when the side-to-bed mapper has been validated
and the calibration metadata and trajectory rows identify that calibrated
coordinate source.

## CLI processing

From `capture/`, process an existing permanent run:

```bash
python application/experiment.py process \
  --run outputs/runs/YOUR_RUN_FOLDER \
  --reference inputs/references/model.gcode
```

The optional low-level reconstruction entry point is useful for isolated
analysis and tests. Do not direct it at a mixed permanent capture folder:

```bash
python application/reconstruct.py \
  --trajectory /path/to/bed_trajectory.csv \
  --output-dir outputs/post_analysis/reconstruction
```

## Interpretation and limitations

Outputs include reconstructed and reference path data, plots, comparison
summaries, and coordinate provenance. Diagnostic metrics are only available
when the reference and observed coordinates can be compared in a common,
justified frame. A pending or limited comparison is not treated as success.
Read the run's summary for the exact calibration, source, and assumptions.

Generated G-code is **analysis-only and not for printing**. It does not infer
unsupported extrusion, feedrate, temperatures, fan settings, acceleration,
retraction, or printer startup/end sequences. Unknown Z/layer information
remains unspecified. Segment-start G0 moves are analysis anchors, not verified
safe travel. Reconstructed files are never a complete replacement for the
original supplied G-code.

See [outputs](OUTPUTS.md), [workflow](WORKFLOW.md), and
[side-camera calibration](side-view.md).
