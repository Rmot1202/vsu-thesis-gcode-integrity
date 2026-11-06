# Alternative upward-view HSV tracker

This is the optional HSV-pin tracker, separate from the printed-black-fiducial
`nozzle_grid_tracker.py`. Both use permanent experiment logging. Run from capture:

```bash
python application/experiment.py capture --view up_auto_view --input 0
```

It records immediately and creates `runs/up_auto_view_date_time/`. Raw video,
annotated video, trajectory.csv, metadata.json and a final calibration snapshot
are saved. On quit/EOF it asks for reference G-code unless --reference or
--no-reference-prompt was supplied, then processes the trajectory in that folder.

## Acquisition and calibration

The default motion detector acquires a moving region inside the detected pin
rectangle and initializes CSRT. This can select a gantry or another moving part.
Use --template /path/to/nozzle.png for appearance-based acquisition, or --manual
and drag an ROI on the original video panel. SHIFT-click calibrates the tip
anchor relative to its box. SPACE pauses, R pauses for selection, A retries
automatic acquisition, and Q finishes. --headless supports unattended processing
with --roi X Y W H or a suitable template.

Default pin HSV bounds are 0,0,50 to 179,60,255 (silver/gray). Adjust --hsv-low
and --hsv-high for your actual markers. The four-candidate geometry builds a
normalized rectangle of --bed-width × --bed-height pixels. Missing pins reuse
its last accepted transform, labeled CACHED. Unlike the black-fiducial tracker,
this alternative does not use the 230×190 mm printer-origin calibration.

CSV image_x/image_y are image pixels; bed_x/bed_y are smoothed warped pixels.
They are not printer bed millimeters. The conservative reconstruction pipeline
therefore uses pixel observations by default. Its comments-only G-code is the
correct result when metric bed evidence is unavailable.

## Useful options

```bash
python application/experiment.py capture --view up_auto_view --input '/path/to/video.mp4' \
  --headless --roi 185 215 42 52 --no-reference-prompt
```

Use --runs-dir to change the root; --save-dir remains an alias. Run filenames
are standardized: --output and --csv are retained for compatibility but do not
rename annotated_video.mp4 or trajectory.csv in the experiment workflow.
The optional legacy --gcode flag produces configured_xy_legacy.gcode and requires
measured --pin-width-mm/--pin-height-mm. It uses a configured feedrate and is
separate from conservative reconstruction; omit it for the thesis observation
workflow. Pin coordinates do not automatically match the machine origin.

See [run commands](RUN_COMMANDS.md), [workflow](WORKFLOW.md), and
[output interpretation](OUTPUTS.md). The implementation remains lightweight
OpenCV/NumPy without YOLO.
