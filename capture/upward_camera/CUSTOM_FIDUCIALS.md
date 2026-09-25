> Run commands below from the **capture root**, not from this camera subfolder. Shared motion/LED settings are in `../tracking_common.py`. See [current run guide](../RUN_COMMANDS.md).

# Custom black fiducial calibration

The complete implementation is in [nozzle_grid_tracker.py](nozzle_grid_tracker.py).
Run from the `capture` directory with your virtual environment active:

```bash
python upward_camera/nozzle_grid_tracker.py
```

This updates the original grid tracker, not `auto_nozzle_tracker.py`. No ArUco
API or marker dictionary is used. Existing CSRT/KCF creation, nozzle ROI
selection, and bounding-box-based tip estimation are retained.

## Physical coordinates

The bed is 230 × 190 mm, with front-left origin and positive Y toward the rear.
Marker-center ordering is always FL, FR, RR, RL:

| Label / ID | X mm | Y mm |
| --- | --- | --- |
| FL / 0 | 6 | 6 |
| FR / 1 | 206 | 6 |
| RR / 2 | 206 | 166 |
| RL / 3 | 6 | 166 |

These are approximate inputs, so the resulting measurements are approximate.
Verify they describe marker centers: an 18 mm square centered 6 mm from an edge
extends 3 mm past that edge. If your coordinates describe a printed corner or
other feature, replace them with the measured centers before quantitative use.
The code keeps your supplied coordinates unchanged.

## Detection and orientation

Grayscale thresholding isolates dark PLA. Morphological closing joins nearby
cells. Convex quadrilateral footprints supply outlines; diagonal intersections
estimate geometric centers independently of the pattern's black-pixel balance.
Candidate groups are checked against known marker size, quadrilateral geometry,
relative image areas, temporal position, and several stable detection frames.

This does not decode the unique patterns. It assigns labels by image position.
At the top of the script, `IMAGE_CORNER_LABELS` lists the physical labels that
appear at image TL, TR, BR, BL. Its default `('RL', 'RR', 'FR', 'FL')` assumes the
front edge is at the bottom of the camera view. Verify the live labels before
using coordinates. Change this setting for a mirrored or rotated camera.
Strong camera rotation can make geometric corner ordering ambiguous.

All detection settings are at the top. Press `d` to inspect the mask and green
candidate outlines. Adjust `BLACK_THRESHOLD`, `CELL_JOIN_KERNEL`, and area
limits to the actual footage. The detector needs a recoverable four-sided
outer footprint. If empty boundary cells or disconnected blocks prevent that,
the footprint model may need adapting to photographs of the actual patterns.
Other dark square objects can still create false detections; there is no
pattern recognition guarantee without pattern templates or a specified layout.

## Calibration and overlays

Each accepted frame produces an image-to-millimeter homography directly from
the four centers. The full bed boundary is projected back onto the camera
image. The top-down view displays front-left at bottom-left. Nozzle image
coordinates and bed X/Y are shown on the original view.

Missing markers normally disable millimeter output. A bounded three-marker
fallback now supports temporary single-marker occlusion, as described below. Press `r`
after a large scene shift to discard the prior-position constraint. Nozzle
tracking continues independently of calibration availability.

A moving Z bed changes perspective; calibration refreshes while markers remain
visible and within the configured shift limits. This is still a planar mapping:
the tip must be near the marker plane for accurate XY. A 0.20 mm marker height,
nozzle height, angled camera, and lens distortion can introduce errors. No Z
position or nozzle parallax correction is inferred.

## Controls and output

- `s`: select/reselect the nozzle with the original ROI workflow.
- `v`: start/stop recording.
- `r`: reset calibration, retaining the nozzle tracker.
- `d`: toggle the diagnostic mask.
- `q`: quit and release output files.

Recordings go into `captures/YYYY-MM-DD/HH-MM-SS_microseconds/` and contain
`annotated.mp4`, `grid.mp4`, `trajectory.csv`, and `calibration.json`. CSV rows
include tracking/calibration flags, pixel coordinates, bed millimeters, and the
current homography for that frame. Missing coordinates are blank. This replaces
the original pin-relative CSV schema. This script does not export G-code.

Synthetic checks cover angled black patterned squares, coordinate conversion,
marker loss, reacquisition, and degenerate geometry. Real printed-pattern
recognition and measurement accuracy require checking actual camera footage.

## Recording starts on nozzle movement

After pressing `s` and selecting the nozzle, recording automatically starts when
the tracked tip moves at least 5 image pixels from its initial position for
three consecutive valid frames. Settings at the top of the script are
`AUTO_RECORD_ON_MOVEMENT`, `MOVEMENT_THRESHOLD_PX`, and
`MOVEMENT_CONFIRM_FRAMES`. Camera motion or tracker drift can also trigger it.

Recording includes the triggering frame and continues through nozzle pauses or
tracking loss. It does not automatically stop when the nozzle rests. Press `v`
to stop (also disarms automatic restart), `a` to re-arm, or `q` to quit. `v` can
also start recording manually. Selecting a new ROI or losing tracking resets
the motion baseline. No calibration is required to trigger recording; missing
bed coordinates remain blank. There is no pre-trigger video buffer.

The camera displays AUTO ARMED, AUTO OFF, or RECORDING. Files still go into the
existing dated session folders. Calibration metadata records the start trigger
and movement threshold settings.

## Red/blue head lights and temporary marker occlusion

Select the nozzle/head ROI with `s`, including the head lights. The existing
box-relative tip estimate must align with the actual nozzle tip; adjust
`NOZZLE_BOX_X_FRACTION` and `NOZZLE_BOX_Y_FRACTION` if necessary. The program
learns a separate pixel offset from each visible red/blue light to that estimated
tip while CSRT is valid. It does not treat the light itself as the nozzle tip,
and it does not infer temperature from color.

Known lights can then support position estimates through short CSRT failures.
The display reports `LED_red`, `LED_blue`, or `CSRT`, and outlines the detected
light in yellow. Unknown colors need a valid CSRT estimate to learn their own
offset. Select the ROI again to reset learned offsets. Ambiguous same-color
blobs are rejected. HSV/area/search thresholds and the 90-frame CSRT-loss limit
are settings at the top of the script. A small saturated halo around a white
LED core may be detected, but a completely washed-out light may not be.

LED offsets are image-space approximations: head rotation, changing scale,
reflections, or inaccurate CSRT initialization can introduce error. They are
not a substitute for verifying that the magenta point marks the physical tip.

After a complete four-marker calibration, three uniquely matched markers can
support a temporary estimate of the fourth. An affine change relative to the
last complete image geometry predicts the missing center. A homography is then
computed using that predicted point. Three centers alone cannot determine an
arbitrary new perspective transform. Large motion/scale changes, ambiguous
matches, fewer than two matches, and estimates older than five seconds are
rejected. Increase `OCCLUSION_MAX_SECONDS` only with awareness of drift and bed
Z changes. No estimated frame becomes the new full-calibration baseline.

The display labels this `ESTIMATED (3/4)`, with the missing outline in
orange. Four markers restore full calibration after the normal confirmation
period. CSV adds `calibration_state`, `visible_markers`, and `tracking_source`;
`calibration_valid=1` means a mapping is available, including an estimated one.
Filter by `calibration_state=CALIBRATED (4/4)` when you need only complete detections.

Synthetic tests cover red-to-blue offset learning, LED-only motion, light loss,
missing-corner estimation, insufficient markers, and estimate timeout. Actual
LED appearance and printed-marker recognition still need camera validation.

## Two visible markers

With `TWO_MARKER_FALLBACK_ENABLED = True` (default), two uniquely matched
markers can estimate the two covered corners after a full four-marker
calibration. The display shows `ESTIMATED (2/4)`; both inferred outlines
are orange. CSV records `visible_markers=2` and the estimated calibration state.

Two centers determine only translation, rotation, and uniform scale relative
to the last complete calibration. They cannot recover changes in perspective
or independent horizontal/vertical scale. This is less reliable than the
three-marker fallback, especially during bed Z movement or camera movement.
The existing five-second limit since the last full calibration and small-motion
checks still apply. Pairs closer than `TWO_MARKER_MIN_SEPARATION_PX` (60 pixels)
are rejected. With one or zero markers, no millimeter coordinates are produced.
Full calibration is required again after timeout; estimates never renew it.

## Full-snapshot fallback implementation

`Calibration` stores independent copies in `last_full_points`,
`last_full_quads`, `last_full_time` (monotonic seconds), and
`last_full_homography`. Only a confirmed four-marker observation replaces
these fields. Startup and full recovery use `CALIBRATION_CONFIRM_FRAMES`
(default three) consistent full detections before accepting a new snapshot.

`build_similarity_transform_from_two_points()` derives scale and rotation from
the dot/cross products of the corresponding old/new marker-pair vectors, then
solves translation using the first correspondence. `estimate_from_two_markers()`
and `estimate_from_three_markers()` transform the full snapshot using similarity
and affine mappings respectively. `apply_affine_to_points()` handles both centers
and quads. Measured centers/quads override transformed values for visible markers.
Label indices always remain FL, FR, RR, RL; partial detections are matched to
snapshot locations, never re-sorted as image corners.

Scale is bounded using singular values; rotation, total displacement from the
full snapshot, pair separation, homography condition number, and projective
horizon checks reject unsafe geometry. Estimates never renew the snapshot time.
With fewer than two reliable matches the mapping is invalid immediately, with
no stale hold, because this printer's bed can move in Z.

Statuses are `CALIBRATED (4/4)`, `ESTIMATED (3/4)`, `ESTIMATED (2/4)`, and
`MARKERS NOT FOUND`. Estimated outlines are orange and labels append `est`.
Nozzle tracking and LED logic are unchanged by this refactor.
