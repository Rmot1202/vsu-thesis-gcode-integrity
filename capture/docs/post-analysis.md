# Post-record printer-head tracking

`printer_head.py` detects the `printer_head` class using custom trained YOLO
weights and associates detections by distance and size across frames. The former
`yellow_warning_label.py` was renamed; update external imports and commands.
The reusable package entry point remains `post_analysis.process_video()`.

From the repository root (`code/`):

```bash
capture/.venv/bin/python capture/backend/post_analysis/printer_head.py \
  --video "/path/to/movie.mov" --model "/path/to/best.pt" \
  --output-dir capture/outputs/post_analysis/head_tracking --device cpu
```

Install `backend/post_analysis/training/requirements.txt` first. Weights must contain a class named
`printer_head`; base pretrained COCO weights are insufficient. Click the detected
head near its center, then SHIFT-click the physical nozzle tip. SPACE pauses and
Q stops processing. The nozzle position is an estimate using a head-width-scaled pixel offset
from the detected head center, so changes in apparent head size or pose can affect
accuracy. Detection and association may fail during occlusion or rapid motion.

Outputs:

- `post_analysis_printer_head_trajectory.csv`: head center and estimated nozzle
  coordinates per frame, `tracking_valid`, and `tracking_source`. Lost frames
  have blank coordinates, with no stale position emitted.
- `post_analysis_printer_head_annotated.mp4`: detected head and nozzle overlays.
- `POST_ANALYSIS_PRINTER_HEAD.json`: weights, confidence, calibration, and source.

Capture outputs remain separate. Existing analysis outputs require `--overwrite`
to replace. For headless use, supply `--no-gui --head-x X --head-y Y --tip-x X
--tip-y Y`, using first-frame image coordinates. API callers supply `model=...`,
`marker_click=(head_x, head_y)`, and `nozzle_tip_click=(tip_x, tip_y)`.
Use `--confidence 0.25` to adjust the detection threshold.

For dataset preparation, draw the whole printer head using
`backend/post_analysis/training/prepare_dataset.py`; after training, add
`--model /path/to/best.pt` to review predicted boxes.

Pixel-size camera motion is exported alongside the image trajectory. The first
selected head box defines the reference width. Each valid frame records
`head_width_px`, `head_height_px`, `head_scale_ratio` (current/reference width),
and `camera_distance_ratio` (reference/current width). A distance ratio of 0.8
means the head is estimated to be 20% closer than in the first frame. Missing
tracks leave all size/depth fields blank.

To also estimate millimeters, add `--initial-distance-mm 500` (replace 500 with
your measured first-frame optical-axis distance from the camera to the tracked
head feature), or pass `initial_distance_mm=500` to `process_video()`. This adds
`camera_distance_mm` and `motion_toward_camera_mm`; positive motion means toward
the camera, negative means away. Without that calibration, millimeter fields
remain blank. The nozzle offset scales by current/reference head width.

These are pinhole estimates using the detected **head box**, not a measured
nozzle opening. Keep the camera and zoom fixed and the head orientation constant.
Box jitter, changing pose, and occlusion can mimic depth changes. Camera depth
is kept separate from printer Z and bed XY; reconstruction does not automatically
map it onto a printer axis. Existing trajectory CSVs must be regenerated from
the video to obtain size measurements.
