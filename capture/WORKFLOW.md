# Persistent experiment workflow

**Treat each run folder as a permanent experiment record once it has been created.**

## 1. Startup cleanup

The selected root defaults to `capture/runs/`, anchored to the code location.
At capture/import startup, cleanup examines immediate child folders. A folder
is deleted only when it has `.test_run.json` containing:

```json
{"kind": "print_capture_test_run", "delete_on_startup": true}
```

Folders with `.experiment.json` are never cleanup candidates. Unmarked folders,
symlinked folders and symlinked test markers are skipped. Deletions are logged
in `startup_cleanup.jsonl`. Use the test marker only on disposable experiment
folders whose entire contents may be deleted.

This distinction resolves the two requirements: remove old disposable testing
artifacts, but never erase newly created permanent experiments at the next
startup. New capture/import folders receive protection before recording begins.
No run is deleted on completion or processing failure. Startup does not scan or
purge historical `captures/` directories.

The one-time migration removes only the previously generated
`reconstructions/side_square_demo` and `reconstructions/synthetic_multilayer`
test outputs, after verifying their managed-file manifests. Older camera
recordings and reference fixtures are retained.

## 2. Capture

`experiment.py capture --view ...` dispatches to the matching camera module.
`side_view` and `up_view` begin a new record on movement or manual V; the HSV
alternative `up_auto_view` starts immediately. Every record uses the name
`{view}_{YYYY-MM-DD}_{HH-MM-SS_microseconds}`. Multiple recordings during one
camera session create separate folders.

The video/CSV writers run while the capture is active. Raw frames are saved
alongside annotations, so later labeling and analysis can use unmodified video.
Frames paused for calibration or selection are not duplicated. Encoded video
uses nominal/source FPS; CSV timing is the better elapsed-time record when
processing slows down. The current HSV CSV lacks per-frame timestamps.

## 3. Stop and reference prompt

V stops recording, or Q/EOF ends the session. Writers close before the terminal
prompt asks for an original G-code path. Enter skips it and leaves comparison
pending. `--reference` supplies it in advance; `--no-reference-prompt` avoids
interaction. Noninteractive stdin also leaves missing references pending.
Interrupted captures are retained and processed when possible without prompting.

A supplied reference is copied to `original_reference.gcode`, making the record
self-contained. Missing/invalid references cannot delete the captured data.
Processing failures are recorded in metadata; correct the input and run
`experiment.py process --run ...` later.

## 4. Reconstruction and comparison

The shared pipeline loads trajectory/evidence, chooses the justified coordinate
space, optionally aligns measured bed coordinates, segments gaps and motion,
organizes supplied layers, then writes paths, plots and summaries. Neither the
reference nor a known test shape fills unsupported camera measurements.

Reference commands supply a comparison path, not observed motion. Compatible
bed-mm data can be overlaid and compared by supplied layer labels. Uncalibrated
side data receives separate pixel/reference panels and no invented XY commands.
No reference leaves reference outputs as explicitly unavailable placeholders.

`comparison_status` is pending without a reference, available when reference
and coordinate evidence support diagnostics, or limited_coordinate_evidence
when they do not. Available does not guarantee matched layer labels, correct
calibration, or that the recording shows the supplied print; read the summaries.

## 5. Permanent preservation and reprocessing

Capture files remain at the run root. Reprocessing replaces only files listed
in `.reconstruction_manifest.json`. Previous analysis moves into
`analysis_history/<timestamp>/`; replaced reference/evidence inputs are copied
into `input_history/`. Unknown files cannot be silently overwritten by the
analysis publisher. Source captures outside the run remain untouched.

The low-level `reconstruct.py --output-dir ...` tool still exists for isolated
analysis/testing and archives its entire managed output on rerun. Prefer
`experiment.py` for permanent integrated records. Never point the low-level
command at a mixed capture folder.

## Recovery

If power is lost, metadata may remain `recording`, and MP4 finalization may be
incomplete. Inspect the files before recovery; automatic startup never deletes
such protected runs. Do not manually mark an active capture finished.
