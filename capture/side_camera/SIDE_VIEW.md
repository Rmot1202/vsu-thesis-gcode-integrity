> Run commands below from the **capture root**, not from this camera subfolder. Shared motion/LED settings are in `../tracking_common.py`. See [current run guide](../RUN_COMMANDS.md).

# Separate side-view tracker

`side_nozzle_tracker.py` leaves both existing trackers unchanged. It imports
CSRT, LED assistance, and motion-trigger helpers from `../tracking_common.py` at the capture root. No YOLO is used.

From the `capture` directory with the existing virtual environment active:

```bash
python side_camera/side_nozzle_tracker.py --input 0
```

Use `--input /path/to/video.mp4` for a recording, or `--no-led` to disable light
assistance. Frame coordinates are reported immediately after selecting the head.

1. Press S and draw a box around the head, including its lights. Press ENTER.
2. The view pauses. SHIFT-click the visible physical nozzle tip; it can be
   outside the head box. This adjusts the box-relative tip offset.
3. Press SPACE to resume. Confirm the magenta tip marker follows the tip.
4. Nozzle motion triggers recording using the existing 5-pixel/3-frame rule.
5. V starts/stops recording; stopping disarms automatic restart. A re-arms it.
   Q quits. SPACE pauses/resumes. Paused frames are not duplicated in outputs.

## Optional side-plane millimeters

A side view cannot reliably provide bed X and Y from nearly collinear bed
markers. This script does not use them or estimate missing bed markers.

For metric measurements, place a known, stationary rectangle in the vertical
plane of the nozzle motion being measured. Do not use the 230 × 190 mm bed
size as this rectangle's dimensions. For example, if your actual reference is
100 mm wide and 60 mm tall:

```bash
python side_camera/side_nozzle_tracker.py --reference-width-mm 100 --reference-height-mm 60
```

Press C and click the reference's top-left, top-right, bottom-right, bottom-left
corners. Then press SPACE. R clears/cancels calibration. The coordinates are
`side_horizontal_mm` and `side_vertical_mm`, with origin at reference bottom-left
and vertical increasing upward. They are not machine X/Y/Z. The homography is
fixed until you recalibrate; moving the camera or reference invalidates it.

Choose a view that shows the physical tip against a contrasting background.
Out-of-plane nozzle motion changes scale and introduces parallax. Accurate
metric conversion requires motion near the calibrated plane. A side camera
alone does not recover hidden depth, and this implementation does not track the
bed surface or calculate the nozzle-to-bed gap as the bed moves in Z.

## Saved files

Each recording goes into `captures/side_camera/YYYY-MM-DD/HH-MM-SS_microseconds/`:

- `side_annotated.mp4`: tip, head box, image trajectory, coordinates, status.
- `side_trajectory.csv`: source frame, elapsed time, pixel coordinates, optional
  side-plane millimeters, validity flags, and each frame's homography.
- `side_run.json`: starting settings and coordinate interpretation.

Use `--save-dir` to choose another root. Missing metric coordinates remain blank;
there is no G-code export. Wall-clock CSV timing includes interactive pauses;
video contains only processed, unpaused frames at source FPS. A fixed LED offset
is approximate and should be checked visually. This script adds a side-view
measurement path; it does not validate physical measurement accuracy by itself.

## Yellow warning-label assistance

Yellow assistance is enabled by default. Select a head ROI that contains both
separate yellow warning labels, then SHIFT-click the physical tip while paused.
After resuming, the script learns each label's offset from the CSRT tip estimate.
Yellow outlines labeled Y1/Y2 show matched regions. The status and CSV identify
`YELLOW_2/2` or `YELLOW_1/2` when labels supply the tip position.

Both labels must be separately detected to initialize. Once learned, either one
can help during partial occlusion. Label-only tracking is limited to 60 frames
without a valid CSRT result. Ambiguous yellow blobs, large area changes, and
inconsistent two-label tip estimates are rejected. The code falls back to
LED/CSRT estimates when yellow matching fails; it does not infer temperature.

Use `--no-yellow` to disable this feature. The HSV range, area limits, matching
radius and loss limit are settings at the top of `side_nozzle_tracker.py`.
Yellow defaults are H=18–40, S=90–255, V=80–255 in OpenCV HSV. These may need
adjustment for your lighting. Keep both labels within the ROI and distinguishable
from yellow background objects. If they merge into one region, initialization
will wait rather than assign two invented labels.

This uses fixed image-space offsets, not exact tip detection or head pose
estimation. Scale/rotation changes and partially covered labels can move their
apparent centers and introduce measurement error. Check the magenta tip point
throughout motion, and reselect/recalibrate if it is incorrect. No extra
libraries or trained models are required.

## Automatic start and separation from other views

Automatic recording is already armed at launch. Select the head with S, set its
tip with SHIFT-click, then press SPACE to resume. No V press is needed: movement
of at least 5 pixels for three consecutive valid frames starts recording.
`AUTO ARMED - waiting for movement` means ready; `RECORDING` means saving.
Recording continues through pauses in nozzle movement. V stops and disarms;
A re-arms. Q exits. Tracking must be initialized; auto-start does not find the
head automatically.

New outputs are separated as follows (under `capture/captures`):

```text
side_camera/YYYY-MM-DD/time/
  side_annotated.mp4
  side_trajectory.csv
  side_run.json
upward_camera/bed_view/YYYY-MM-DD/time/
  bed_annotated.mp4
  bed_grid.mp4
  bed_trajectory.csv
  bed_calibration.json
auto_upward_camera/bed_view/YYYY-MM-DD/time/
  auto_bed_*.mp4
  auto_bed_*.csv       (when requested)
  auto_bed_*.gcode     (when requested)
  auto_bed_run.json
```

CSV rows and JSON metadata contain a `view_mode` field. Side-view metadata also
records whether recording started manually or from nozzle movement. Custom
save directories still apply; filenames and metadata retain their view labels.
Old captures are not renamed or moved because their view cannot be assumed
from generic historical filenames. These conventions apply after restart.
