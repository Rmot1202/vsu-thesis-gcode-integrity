> **Current workflow:** Use [../RUN_COMMANDS.md](../RUN_COMMANDS.md). New recordings are permanent `runs/{view}_{date}_{time}/` experiments with `raw_video.mp4`, `annotated_video.mp4`, `trajectory.csv`, and `metadata.json`. After recording, answer the reference prompt in the terminal; analysis is saved in the same folder. Shared lifecycle code is `../experiment_log.py`.

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

## Permanent output files

Recordings now go into `runs/side_view_YYYY-MM-DD_HH-MM-SS_microseconds/`.
Each contains `raw_video.mp4`, `annotated_video.mp4`, `trajectory.csv`, and
`metadata.json`, followed by reconstruction/plots/summaries after recording ends.
See [OUTPUTS.md](../OUTPUTS.md) for the complete manifest. No measured bed XY
is invented from the side view. CSV time includes interactive pause duration;
video includes only processed unpaused frames.

## Red head-strip assistance

Red-strip assistance is enabled by default and takes priority over the red/blue
LEDs when it finds a unique match. Put one short, wide,
matte-red rectangle on a fixed, cool part of the head housing, visible inside
the selected ROI. After selecting the head with S, SHIFT-click the physical
nozzle tip once. The strip-to-tip offset is learned from the strip in that ROI;
the red outline and `RED_STRIP` status show when it is supplying the tip
estimate. Use `--no-red-strip` to turn it off. The strip tracker uses a large,
wide-rectangle filter to distinguish the marker from the smaller red LED.

Tune color, shape, and tracking limits near the top of
`side_nozzle_tracker.py`: `RED_STRIP_HSV_LOW_1` / `RED_STRIP_HSV_HIGH_1` and
`RED_STRIP_HSV_LOW_2` / `RED_STRIP_HSV_HIGH_2` cover red at both ends of
OpenCV's hue scale (defaults H=0–10 and 170–179, S=70–255, V=60–255).
`RED_STRIP_MIN_AREA` / `RED_STRIP_MAX_AREA` set contour size;
`RED_STRIP_MIN_ASPECT_RATIO` rejects shapes that are not wide rectangles;
`RED_STRIP_SEARCH_RADIUS_PX` limits the match around the predicted strip
position; and `RED_STRIP_MAX_WITHOUT_CSRT_FRAMES` limits fallback duration. If
multiple similar red rectangles appear in the ROI at
initialization or near the predicted position, the tracker rejects the match.

The strip estimates the tip using a fixed pixel offset. It does not measure
depth, compensate for perspective changes, or determine printer bed X–Y. The
side-view homography above remains a side-plane calibration only; to retain
bed coordinates while a bed fiducial is occluded, use the upward-camera bed
tracker and its bed-plane calibration instead. Do not treat this head marker as
a replacement for bed fiducials.

## Automatic start and post-record processing

Auto-start is armed at launch. Select the head, set the tip and resume.
Five pixels of motion for three valid frames starts recording. V stops and
disarms; A re-arms. Q exits. After stop, the terminal asks for reference G-code;
press Enter to leave comparison pending. Multiple recordings become separate
permanent side_view folders. --no-reference-prompt suppresses the question.
