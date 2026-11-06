> **Current workflow:** Use [RUN_COMMANDS.md](RUN_COMMANDS.md). New recordings
> are permanent `outputs/runs/{view}_{date}_{time}/` experiments. Historical
> filename examples below describe older captures, not the current layout.
> Shared lifecycle code is in `backend/experiment_log.py`.

> Run commands below from the `capture/` root. Shared motion/LED settings are
> in `backend/tracking_common.py`. See [current run guide](RUN_COMMANDS.md).

# Nozzle tracker: purpose and code explanation

## Purpose

[auto_nozzle_tracker.py](../cameras/top/auto_nozzle_tracker.py) measures the visible motion of a
3D printer nozzle from a webcam or recorded video. Four reference pins define a
rectangle on the bed. The script uses their image positions to correct the
camera perspective, estimate nozzle positions in that rectangle, and display
the trajectory in both the camera image and a top-down view.

It supports visual inspection and trajectory analysis, with optional CSV data
and XY G-code export. It does not control the printer or reconstruct a complete
print program from video.

The script is separate from [nozzle_grid_tracker.py](../cameras/top/nozzle_grid_tracker.py).
It detects pins automatically and does not read the original script's saved
`calibration_*.json` files.

## Processing sequence

For each source frame, the script:

1. Detects silver pin candidates using HSV color thresholds and blob geometry.
2. Selects four plausible pins and computes a perspective transform, or keeps
   the last accepted transform when detection is incomplete or inconsistent.
3. Acquires or tracks a nozzle bounding box using lightweight image processing
   and OpenCV's CSRT tracker.
4. Estimates one nozzle tip point near a box-relative anchor.
5. Maps that point into top-down bed coordinates and smooths it.
6. Draws the camera view, warped bed view, markers, and trajectory.
7. Writes the annotated video and any requested CSV and G-code records.

Only OpenCV and NumPy are required beyond Python's standard library. CSRT
requires `opencv-contrib-python`; dependencies are listed in
[requirements.txt](../requirements.txt).

## How pin detection works

`detect_pins()` converts BGR camera frames to HSV. For silver pins, the defaults
accept any hue, low saturation, and a range of brightness:

```text
Lower HSV: (0, 0, 50)
Upper HSV: (179, 60, 255)
```

The threshold creates a binary mask. Morphological opening removes small noise,
and closing fills small gaps. The function finds contours and filters them by
area, bounding-box dimensions, and circularity:

```text
circularity = 4 × pi × contour_area / perimeter²
```

A circle has circularity near 1. Each candidate's center is calculated from
contour moments. At most 12 candidates are retained for further evaluation.

Gray is not a unique color on most printers. Other metal parts can pass the
same threshold. Lighting, reflections, apparent pin size, and background all
influence detection; the defaults are a starting point.

## Pin ordering and calibration

`sort_pins()` orders four centers as top-left, top-right, bottom-left, and
bottom-right. It sorts around their center, then uses the smallest image X+Y
value to select the first corner. This assumes a roughly upright rectangle in
the camera view. Identical pins cannot establish the printer's physical axis
orientation by themselves.

`compute_homography()` rejects collapsed, nonconvex, very small, or excessively
skewed quadrilaterals. It computes a 3×3 homography mapping the image corners to:

```text
TL → (0, 0)              TR → (width - 1, 0)
BL → (0, height - 1)     BR → (width - 1, height - 1)
```

`Calibration.update()` evaluates combinations of four candidates. It considers
circularity, pin-area consistency, and distance from the previous accepted pin
positions. `--pin-max-shift` defaults to 25 image pixels. A valid selection
replaces the stored calibration.

When no combination passes, the previous transform remains available. The UI
shows `LIVE` for a calibration accepted in the current frame, `CACHED` when an
older one is reused, or `SEARCHING` before any calibration exists. There is no
expiry time on a cached transform, so the camera and reference rectangle must
remain fixed relative to each other. Restart after changing that geometry.

## Nozzle acquisition and tracking

`NozzleTracker` supports three ways to choose the initial object.

| Method | How it chooses a box | Main limitation |
| --- | --- | --- |
| Motion, default | Learns a background, then finds a persistent moving region within the pin rectangle | Can select a moving bed, gantry, hand, or part |
| Template, `--template` | Searches for a cropped reference image using normalized squared-difference template matching | Sensitive to scale, lighting, and appearance changes |
| Manual | Uses a box dragged on the original view, or `--roi X Y W H` | Requires a suitable user selection |

Motion acquisition waits through startup frames and requires three nearby
candidate proposals before initialization. It selects the largest moving blob
that passes the configured area limits. Template acquisition also requires
persistent proposals. These methods do not semantically recognize a nozzle.

`create_tracker()` creates CSRT using either OpenCV's main or legacy namespace.
CSRT updates the object's bounding box on subsequent frames. `clip_box()` keeps
boxes inside the image and rejects boxes that are too small.

On a reported tracking failure, the box is cleared and automatic acquisition
can retry, unless `--manual` is active. A tracker may drift onto the wrong
object while still reporting success; inspect the displayed box.

### Estimating the tip

`refine_tip()` searches near an anchor relative to the bounding box. By default,
`--tip-anchor 0.5 0.9` places the anchor near the lower center. The function
combines dark-pixel and edge evidence with a penalty for distance from that
anchor, returning the highest-scoring pixel as the tip.

This is an image-based estimate, not a guaranteed detection of the physical
nozzle tip. Adjust the ROI and anchor for the camera angle and nozzle appearance.

## Coordinates and smoothing

Three coordinate systems are used:

| Coordinates | Units | Origin and direction | Where used |
| --- | --- | --- | --- |
| Image | Original-frame pixels | Top-left, X right, Y down | Tip marker and CSV image columns |
| Bed view | Warped-view pixels | Top-left pin, X right, Y down | Top-down view and CSV bed columns |
| G-code | Millimeters | Bottom-left pin, X right, Y up | Exported XY moves |

`transform_point()` multiplies an image point by the homography and divides by
the resulting homogeneous scale. Points outside the configured bed view are
excluded from bed coordinates and G-code.

`smooth_point()` applies an exponential moving average (EMA):

```text
smoothed = alpha × current + (1 - alpha) × previous
```

The default `--alpha 0.35` reduces jitter but introduces lag. Larger values
respond faster. Smoothing resets when a bed point is unavailable. Image-space
coordinates are not EMA-smoothed.

The top-down dimensions, default 800×800 pixels, control output resolution;
they do not specify the physical bed dimensions. For G-code, measured pin
spacing supplies the physical scale:

```text
X_mm = bed_x / (bed_width - 1) × pin_width_mm
Y_mm = (1 - bed_y / (bed_height - 1)) × pin_height_mm
```

Measure the rectangle between pin centers rather than assuming nominal printer
bed dimensions. The exported origin is not automatically the machine origin.

## Interface and visualization

`Interface` receives mouse events from one OpenCV window. The left panel shows
the original video, and the right panel shows the warped bed.

Dragging on the left panel pauses playback and selects an ROI directly there.
Display coordinates are converted back to original-frame pixels. A valid
selection initializes CSRT and resumes playback.

| Control | Action |
| --- | --- |
| Drag on left video | Select or correct nozzle ROI |
| `R` | Pause for selection |
| `SPACE` | Pause or resume |
| `A` | Clear the tracker and enable automatic reacquisition |
| `Q` | Finish and close output files |

`draw_views()` builds the combined display. `draw_trail()` draws trajectory
segments and breaks them where positions are missing. The display history is
bounded by `--trail-length`, default 3,000 entries. CSV and G-code stream data
throughout the run rather than using only this display history.

## Permanent outputs and shared reconstruction

`create_run_folder()` delegates to `experiment_log.Experiment.create()`. Every
capture is stored in `runs/up_auto_view_date_time/` with a permanent marker,
raw_video.mp4, annotated_video.mp4, trajectory.csv, metadata.json, and final
calibration_snapshot.json. Filenames are standardized across views; metadata
and folder names identify the camera. CSV image positions and warped-bed
positions are pixels, not machine millimeters.

`finish_capture()` closes the experiment lifecycle: it records completion,
asks for original G-code (or skips in noninteractive mode), and runs the shared
reconstruction pipeline. Analysis files remain in the run folder; reprocessing
archives earlier analysis there. No protected run is deleted on startup.

The reference file is copied as original_reference.gcode. It is used for
comparison, not to fill missing camera measurements. The optional old GCodeWriter
is available only through the legacy --gcode setting and is not the conservative
reconstruction exporter. See [OUTPUTS.md](OUTPUTS.md) for units and meaning.

## Code map

| Function or class | Responsibility |
| --- | --- |
| `hsv_value()` | Parse and validate HSV command-line values |
| `detect_pins()` | Build the mask and extract pin candidates |
| `sort_pins()` | Assign image corner order |
| `compute_homography()` | Validate geometry and calculate the perspective mapping |
| `Calibration` | Select pin groups and preserve the last valid mapping |
| `transform_point()` | Map one image point into the bed view |
| `smooth_point()` | Apply EMA smoothing |
| `clip_box()`, `create_tracker()` | Validate ROIs and construct CSRT |
| `refine_tip()` | Estimate the tip within a tracked ROI |
| `NozzleTracker` | Acquire, initialize, and update nozzle tracking |
| `Interface` | Handle in-window selection and pause state |
| `draw_trail()`, `draw_views()` | Render trajectories and the combined view |
| `GCodeWriter` | Convert bed pixels to millimeters and write XY moves |
| `parse_args()` | Read options and validate configuration |
| `create_run_folder()` | Route artifacts into a unique dated folder |
| `main()` | Open input, process frames, write outputs, and release resources |

## Practical limits

A homography models one plane. The observed tip and reference pin centers must
be close to the same plane for accurate measurement. Height differences create
parallax. Lens distortion is not corrected by this implementation.

Silver pins may blend into gray backgrounds or merge with nearby surfaces in the
mask. Four plausible blobs can still be the wrong four objects. A moving bed
or camera can invalidate cached calibration or confuse motion acquisition.

The script was checked with synthetic footage, including pin occlusion and
exports. That does not establish real-world measurement accuracy. Assess the
selected pins and nozzle visually, then compare exported distances with known
movements before using the data for quantitative conclusions.

For copy-and-paste commands, see [RUN_COMMANDS.md](RUN_COMMANDS.md). For a shorter
operating guide, see [auto-tracker](auto-tracker.md).

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
