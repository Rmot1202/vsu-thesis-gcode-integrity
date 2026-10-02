# Connected experiment entry point

Use `python experiment.py capture --view side_view` or `--view up_view` from
the capture root. Recording, reference prompting, reconstruction and preservation
are connected automatically. To add reference/evidence later use
`python experiment.py process --run runs/YOUR_RUN --reference /path/to/model.gcode`.
See [run commands](../RUN_COMMANDS.md), [workflow](../WORKFLOW.md), and
[outputs](../OUTPUTS.md). New experiments remain permanent. Only explicitly
marked disposable tests are eligible for startup cleanup.

The low-level CLI documented below remains available for independent analyses;
it is not the preferred capture workflow and does not move raw recordings.

# General camera-path reconstruction

This framework reconstructs supported geometric motion from trajectory data.
It supports arbitrary open/closed paths, multiple islands, classified infill and
travel, and ordered multi-layer evidence. It does not force observations to
match a square, close loops, fill missing layers, or estimate printer settings.
No YOLO or new dependency is needed: it uses the existing NumPy/OpenCV install.

## Run from the capture root

Reference-free, calibrated bed data:

```bash
python reconstruct.py --trajectory /path/to/bed_trajectory.csv \
  --output-dir reconstructions/bed_run
```

Reference-assisted (comparison does not replace measurements):

```bash
python reconstruct.py --trajectory /path/to/bed_trajectory.csv \
  --reference /path/to/model.gcode \
  --states /path/to/states.csv \
  --per-layer-plots --output-dir reconstructions/comparison
```

Side-view proof of concept:

```bash
python reconstruct.py \
  --trajectory captures/side_camera/2026-09-25/17-46-39_551308/side_trajectory.csv \
  --reference /Users/RavenMott1/Downloads/UMS3_square/3D/model.gcode \
  --output-dir reconstructions/side_square_demo
```

Side pixels remain pixels. `reconstructed_path_only.gcode` will contain comments
explaining missing bed XY, not invented motion commands. The plot comparison
uses separate panels rather than overlaying pixels on millimeters. For calibrated
side-plane coordinates use `--coordinates side`; these still cannot become bed
XY. Your recorded side-plane homography measures horizontal/vertical side motion,
not printer X/Y. Depth requires additional sensing or independently justified geometry.

## Input contracts

### Trajectory CSV

Automatic column adapters:

| Meaning | Recognized columns |
| --- | --- |
| Frame identity | `frame_index` or `frame`; otherwise row index |
| Time in seconds | `elapsed_seconds` or `time_seconds` |
| Bed millimeters | `nozzle_x_bed_mm,nozzle_y_bed_mm` or `x_mm,y_mm` |
| Image pixels | `image_u_px,image_v_px`, `nozzle_u_px,nozzle_v_px`, or `image_x,image_y` |
| Side-plane millimeters | `side_horizontal_mm,side_vertical_mm` with `--coordinates side` |
| Optional evidence | `layer_index`, `z_mm`, `motion_state`, `confidence` |

`tracking_valid`, `tracking_ok`, `tracker_valid`, and metric calibration flags
are respected when present. `inside_bed=0` excludes a bed point. Estimated marker
calibration is retained with an `estimated_calibration` provenance tag. Missing
calibration freshness is flagged; the pipeline cannot recover it retrospectively.
Coordinates `bed_x,bed_y` from the HSV automatic tracker are warped-view pixels,
not millimeters, and are deliberately not interpreted as bed XY.

Frame IDs must be unique. Row order is preserved; timestamps are not sorted to
hide capture defects. Invalid observations break paths. Duplicate/decreasing
times, long time gaps, optional large spatial jumps, state/layer changes, and
stationary intervals split segments. They do not by themselves prove travel,
extrusion, or a layer transition.

### Homography JSON

To map raw image coordinates into bed millimeters, supply `--calibration`:

```json
{
  "coordinate_space": "bed_mm",
  "image_to_bed_homography": [[1,0,0],[0,1,0],[0,0,1]]
}
```

The identity above illustrates schema only; replace it with a valid measured
image-to-bed transform. Per-row `image_to_bed_homography` takes precedence when
mapping raw pixels. Existing explicit bed-mm columns are already transformed
and are not mapped twice. A generic `last_homography` or marker snapshot alone
is not assumed to map to millimeters. A side-camera view_mode is rejected in
bed mode even if a homography is supplied. Static calibration cannot compensate
for changing bed Z; prefer each frame's valid calibration.

### Layer and motion evidence

Provide columns in the trajectory or join `--states states.csv` by exact frame ID:

```csv
frame_index,layer_index,z_mm,motion_state,confidence,layer_source,z_source,state_source
100,0,0.3,perimeter,0.95,manual_annotation,measured,manual_annotation
101,0,0.3,travel,0.8,manual_annotation,measured,visual_classifier
200,2,,infill,0.75,manual_annotation,unknown,visual_classifier
```

Allowed states: `unknown`, `print`, `perimeter`, `infill`, `travel`, `pause`.
Unknown remains unknown. Supplied scores are preserved, not calibrated or treated
as ground truth. Missing annotations do not copy their neighbours' labels.
Layer labels are integers and retain encounter order, including revisits and
skipped indices. Reference layers are never copied to camera observations.

Optional `--layers-from-z` groups **classified deposition** points by changing
Z and labels transitions `hypothesized_from_deposition_z`. It ignores travel Z
hops and refuses to mix with explicit layer indices. It is unsuitable for spiral
or nonplanar printing; use explicit evidence there. Without Z or layer evidence,
layer structure stays unknown. No default layer height is invented.

### Registration

Coordinates must already be bed millimeters. `--alignment alignment.json` accepts:

```json
{
  "source_points": [[10,20],[20,20],[10,30]],
  "target_points": [[40,50],[50,50],[40,60]],
  "provenance": "reference_aligned"
}
```

Supply corresponding measured landmarks, not an arbitrary best-looking shape
fit. This uses rigid rotation/translation only, preserving metric scale. It
reports the landmark residual and transform. `reference_aligned` requires a
reference file. Other independent measured correspondences may be tagged
`user_supplied_correspondences`. It cannot promote side pixels into bed XY.
There is no automatic temporal matching or automatic assumption that recordings
show the same print.

## Reference parser

Supports G0/G1, absolute/relative XYZ, M82/M83 extrusion, G20/G21 units, G92
coordinate resets, per-tool extrusion accounting, Cura `;LAYER:`/`;TYPE:` comments,
and G17 incremental-I/J arcs tessellated using `--arc-step-mm` (default 1 mm).
Motion command endpoints are labeled reference evidence. Arcs retain a
`arc_tessellation` tag. R-format arcs, fractional G modes, and non-XY planes are
explicitly unsupported; linearize them before use. This is not a full firmware
emulator. Tool offsets and physical machine kinematics are not reconstructed.
G90/G91 affect XYZ and M82/M83 independently control E in this reader.

Default `--reference-scope print` retains positive-extrusion XY moves; use
`--reference-scope all` to include travel and path-only reference files without
E. Initial unknown position and unsupported coordinate resets produce gaps.
Source extrusion is used to classify reference moves, never exported or copied
into observations. Layers without comments stay unknown in the reference.

## Denoising and gap policy

Default: no smoothing, no interpolation, no spatial jump threshold. Optional:

- `--max-jump 10`: split large jumps and reject isolated spikes supported by two
  consistent neighbours; units follow selected coordinate space.
- `--max-gap-seconds 2`: split long temporal gaps (default 2 seconds).
- `--smooth-alpha 0.35`: EMA within each segment; adds `ema_smoothed` provenance.
- `--interpolate-max-points 2`: opt into linear bridging of short gaps only with
  ordered timestamps, matching state/layer labels, and bounded duration/jump.
- `--min-confidence 0.5`: reject supplied low-confidence points; absent confidence
  remains unknown rather than being fabricated.

Raw/cleaned/final point CSVs let you audit differences. With registration enabled,
raw_xy records the pre-alignment selected coordinate and xy records the aligned
coordinate. Upstream smoothing may already exist in input CSVs; the pipeline
cannot recover original unsmoothed measurements from them.

## Outputs

Always produced:

- `reference_path_only.gcode` (comments-only if reference unavailable).
- `reconstructed_path_only.gcode` (comments-only without valid bed XY).
- `reference_path.png`, `reconstructed_path.png`, `overlay_comparison.png`.
- `reconstruction_summary.txt` and machine-readable JSON.
- `observations_raw.csv`, `observations_cleaned.csv`, `reconstructed_points.csv`,
  and `reference_points.csv`.

When multiple observed layer labels exist:
`reconstructed_layered_path_only.gcode` is also produced. `--per-layer-plots`
adds layer images and, where coordinates are compatible, layer overlays.

Export includes only G0/G1-style coordinates plus metadata comments. It emits
no E, F, temperature, fans, tool changes, acceleration, homing, or startup/end
sequences. Z appears only when supplied; absence is explicitly annotated.
G0 at a segment start is a schematic anchor, not observed safe travel. G1 in an
unknown-state segment describes geometry, not proven deposition. Never run
these analysis files on a printer without a separate validated planning step.

Plots preserve gaps and never close contours automatically. Composite colors
cycle between layers; per-layer images disambiguate larger layer counts. When
coordinate frames match, reference/observation overlays use common bounds.
Per-layer distances are sampled nearest-neighbour diagnostics, not sequence,
extrusion, timing, or calibrated accuracy claims. Missing physical layers cannot
be established just because layer labels are absent.

## Reproducibility and cleanup

Inputs/settings are validated and outputs built in temporary staging before
publishing. Each successful run records input SHA-256 hashes and settings. A
rerun archives the previous managed output directory to the sibling
`<output>_archive/<timestamp>/` then publishes only the current artifacts.
Unmanaged nonempty directories and directories containing added user files are
rejected rather than deleted. Source captures are never cleaned or modified.
Use a dedicated reconstruction output directory, not a capture session folder.

The current implementation parses CSV incrementally but retains point records
in memory for segmentation and auditing (linear memory growth). Comparison
nearest-neighbour calculations are sampled/chunked. For very long captures,
process bounded session files; on-disk/chunked trajectory storage is a future
extension. Current coordinates do not recover occluded motion, temperature,
extrusion quantity, or unobserved Z.

## Validation

```bash
python -m unittest discover -s tests -p 'test_reconstruction.py' -v
python reconstruct.py --trajectory reconstruction/examples/multilayer_observation.csv \
  --reference reconstruction/examples/multilayer_reference.gcode \
  --per-layer-plots --output-dir reconstructions/synthetic_multilayer
```

Examples are explicitly synthetic fixtures, not experimental results. Tests
cover multilayer/open/disconnected paths, missing observations, interpolation,
outliers, calibration, rigid alignment, modal reference parsing, arcs, safe
archiving, and prevention of side-pixel XY export.
