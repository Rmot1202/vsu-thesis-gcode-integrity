# Custom black fiducial calibration

The complete implementation is in [nozzle_grid_tracker.py](nozzle_grid_tracker.py).
Run from the `capture` directory with your virtual environment active:

```bash
python nozzle_grid_tracker.py
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

Missing or rejected markers disable millimeter output immediately. The last
positions guide reacquisition, but an old homography is not reused. Press `r`
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
