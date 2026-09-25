> Run commands below from the **capture root**, not from this camera subfolder. Shared motion/LED settings are in `../tracking_common.py`. See [current run guide](../RUN_COMMANDS.md).

# Automatic nozzle tracker

`auto_nozzle_tracker.py` is separate from the original `nozzle_grid_tracker.py`.
Install dependencies with `python -m pip install -r capture/requirements.txt`.

From the project directory:

```sh
python upward_camera/auto_nozzle_tracker.py \
  --input 0 \
  --hsv-low 0,0,50 --hsv-high 179,60,255 \
  --bed-width 800 --bed-height 800 \
  --output capture/captures/annotated.mp4 \
  --csv capture/captures/trajectory.csv \
  --gcode capture/captures/trajectory.gcode \
  --pin-width-mm 220 --pin-height-mm 220
```

Replace `220` with the measured pin rectangle's center-to-center dimensions.
Use `--input path/to/video.mp4` for a recording. HSV defaults target silver pins using low saturation.
Hue uses OpenCV's 0–179 scale; saturation and value use 0–255. A lower hue
larger than the upper hue supports colors spanning the red hue boundary.

The four pins must form a rectangle on the reference plane and the camera must
remain fixed. Orient the view so the rectangle is roughly upright. Image TL/TR/
BL/BR define the calibration; identical pins cannot identify machine axes or
resolve arbitrary camera rotation. Extra similarly colored objects can confuse
startup calibration. Candidate limits and `--pin-max-shift` can be adjusted.
Missing or geometrically inconsistent pins retain the last accepted transform.
The UI labels that transform CACHED; CSV `homography_valid=1` means a usable
transform exists, including a cached one. Restart after moving the camera.

## Tracking and controls

Automatic mode first learns the scene, then looks for persistent motion within
the pin rectangle and initializes CSRT. This is a heuristic: a moving bed,
gantry, hand, or part can be mistaken for the nozzle. A stationary nozzle may
not initialize. For more reliable automatic acquisition, supply a tightly
cropped nozzle image from the same camera scale using `--template nozzle.png`.
CSRT tracks the selected object and local dark/edge evidence refines a tip near
`--tip-anchor 0.5 0.9` (fractions within the ROI). Adjust this anchor for your view.
Neither CSRT nor the local refinement guarantees a physical nozzle-tip match.

- Drag a box tightly around the nozzle **on the original video in the existing
  window**. Playback pauses on mouse-down and resumes after a valid selection.
- `R`: pause to select/reselect. `SPACE`: pause/resume. `A`: retry automatic
  acquisition. `Q`: quit.
- `--manual`: acquire only through ROI selection.
- `--headless --roi X Y W H`: process without a window using a starting ROI.
- `--headless --template nozzle.png`: unattended template acquisition.

The right panel shows the warped bed; both panels are saved in the annotated
video. The trajectory uses an EMA controlled by `--alpha` and a bounded display
history controlled by `--trail-length`. CSV and G-code stream the full run.
CSV rows are written once per processed source frame, with blank coordinates
when unavailable. Bed coordinates are smoothed view pixels, origin top-left,
Y down. Image coordinates are unsmoothed refined tip pixels. Out-of-bed points
are excluded from bed coordinates and G-code. Video is written at source FPS;
interactive pauses are not recorded as duplicate frames.

## G-code interpretation

Export contains `G21`, `G90`, and `G1 X… Y… F…` with configurable `--feedrate`
(mm/min). The origin is the bottom-left pin; Y increases upward. It contains no
homing, Z, extrusion, heating, printer offsets, or measured motion timing. It is
an XY path for inspection/adaptation, not a ready-to-print reconstruction.
`--gcode-step-mm` suppresses tiny duplicate moves. Tracking gaps receive segment
comments; the next G1 still travels from the preceding position, so review and
adapt those transitions and establish machine origin/clearance before execution.

A homography maps only one plane. Pins and the observed tip must be close to
that plane for accurate measurement; nozzle height and lens distortion cause
errors. Set the physical dimensions from the pins, not nominal bed dimensions.

## Dated capture folders

Each run saves its files in `capture/captures/upward_camera/auto_bed_view/YYYY-MM-DD/HH-MM-SS_microseconds/`.
The default location is beside the script, independent of your terminal folder.
Use `--save-dir /path/to/captures` to choose a different root. `--output`, `--csv`,
and `--gcode` specify filenames inside the run folder; any directory portions
in those arguments are ignored. CSV and G-code remain optional.

Each folder also contains `run.json` with start/end timestamps, run settings,
processed frame count, completion status, and the last accepted pin positions
and homography. Existing captures are left in place.

From inside the `capture` directory:

```bash
python upward_camera/auto_nozzle_tracker.py \
  --input 0 \
  --output annotated.mp4 \
  --csv trajectory.csv \
  --gcode trajectory.gcode \
  --pin-width-mm 220 \
  --pin-height-mm 220
```

Replace the dimensions with your measured pin spacing.

## Tip outside the tracking box or at rest

The CSRT box may track a stable part of the print head while the tip extends
outside it. The tip search now extends around its anchor beyond the box edges.
An anchor outside the camera image produces no tip coordinate.

1. Drag a box around a stable visible part of the print head.
2. Press SPACE to pause at a clear frame, including the resting position.
3. Hold SHIFT and click the actual tip in the left video panel. The tip may be
   outside the tracking box. This sets its position relative to that box.
4. Press SPACE to resume. Repeat after changing the ROI if necessary.

For example, `--tip-anchor 0.5 1.2` places the search below the box at 120% of
its height. The selected anchor is printed in the terminal and saved as
`final_tip_anchor` in `run.json`. Local refinement can shift the point slightly
from the click. The offset assumes a fixed relationship between the tracked
head and tip; it cannot recover a tip hidden behind an object. Motion-based
acquisition needs movement, so manually select the head if it starts at rest.
