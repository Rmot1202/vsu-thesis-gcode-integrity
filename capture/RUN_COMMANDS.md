# Nozzle tracker run commands

Run these commands in a terminal from the project folder.

## Setup

```bash
cd /Users/RavenMott1/Desktop/Thesis/code
source capture/.venv/bin/activate
python -m pip install -r capture/requirements.txt
```

If the virtual environment does not exist, create it first:

```bash
cd /Users/RavenMott1/Desktop/Thesis/code
python3 -m venv capture/.venv
source capture/.venv/bin/activate
python -m pip install -r capture/requirements.txt
```

## Webcam: automatic tracking

```bash
python capture/auto_nozzle_tracker.py \
  --input 0 \
  --hsv-low 0,0,50 \
  --hsv-high 179,60,255 \
  --bed-width 800 \
  --bed-height 800 \
  --output capture/captures/auto_annotated.mp4 \
  --csv capture/captures/auto_trajectory.csv
```

The default HSV range targets silver pins: any hue, saturation 0–60, and
brightness 50–255. These are starting values; tune them for your lighting.
Bright silver reflections are included. For darker pins, try
`--hsv-low 0,0,25`. Other gray printer parts can also pass this color filter;
the detector additionally filters blob size, circularity, and four-pin geometry.
Use `--input 1` to try another webcam.

## Webcam: also export G-code

Replace `220` with the actual horizontal and vertical pin spacing measured
center-to-center in millimeters.

```bash
python capture/auto_nozzle_tracker.py \
  --input 0 \
  --hsv-low 0,0,50 \
  --hsv-high 179,60,255 \
  --output capture/captures/auto_annotated.mp4 \
  --csv capture/captures/auto_trajectory.csv \
  --gcode capture/captures/auto_trajectory.gcode \
  --pin-width-mm 220 \
  --pin-height-mm 220 \
  --feedrate 1200
```

G-code contains XY moves only, with no Z or extrusion. Its origin is the
bottom-left pin. Review calibration, machine origin, clearance, and travel
across tracking gaps before executing it. Feed rate is configured, not recovered
from video.

## Recorded video

Replace the input path with your video file.

```bash
python capture/auto_nozzle_tracker.py \
  --input "/path/to/printer_video.mp4" \
  --output capture/captures/video_annotated.mp4 \
  --csv capture/captures/video_trajectory.csv
```

## Select the nozzle manually in the original view

```bash
python capture/auto_nozzle_tracker.py \
  --input 0 \
  --manual \
  --output capture/captures/manual_annotated.mp4 \
  --csv capture/captures/manual_trajectory.csv
```

Drag a box around the nozzle on the left video panel. No separate ROI window
opens. Pin detection stays automatic.

## Automatic acquisition using a nozzle template

Provide a tightly cropped nozzle image at the same camera scale.

```bash
python capture/auto_nozzle_tracker.py \
  --input 0 \
  --template "/path/to/nozzle_template.png" \
  --output capture/captures/template_annotated.mp4 \
  --csv capture/captures/template_trajectory.csv
```

## Process a recording without a window

Replace the ROI values with the nozzle bounding box in the first frame:
`X Y WIDTH HEIGHT`, in original-image pixels.

```bash
python capture/auto_nozzle_tracker.py \
  --input "/path/to/printer_video.mp4" \
  --headless \
  --roi 185 215 42 52 \
  --output capture/captures/headless_annotated.mp4 \
  --csv capture/captures/headless_trajectory.csv
```

## Controls

- Drag on the original video: select or correct the nozzle ROI.
- `R`: pause for selection.
- `SPACE`: pause or resume.
- `A`: retry automatic nozzle acquisition.
- `Q`: quit and close output files.

Motion-based automatic acquisition can confuse other moving objects with the
nozzle. Check the selected box and correct it by dragging when needed.
Each run gets a new dated folder, so previous runs are preserved.

## List all options

```bash
python capture/auto_nozzle_tracker.py --help
```

The new script detects its own pins; it does not load the old tracker's
`calibration_*.json` files. See [AUTO_TRACKER.md](AUTO_TRACKER.md) for calibration
and coordinate details.

## Dated capture folders

Each run saves its files in `capture/captures/YYYY-MM-DD/HH-MM-SS_microseconds/`.
The default location is beside the script, independent of your terminal folder.
Use `--save-dir /path/to/captures` to choose a different root. `--output`, `--csv`,
and `--gcode` specify filenames inside the run folder; any directory portions
in those arguments are ignored. CSV and G-code remain optional.

Each folder also contains `run.json` with start/end timestamps, run settings,
processed frame count, completion status, and the last accepted pin positions
and homography. Existing captures are left in place.

From inside the `capture` directory:

```bash
python auto_nozzle_tracker.py \
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
