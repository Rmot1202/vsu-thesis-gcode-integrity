# Square print-path reference and visual observation

`square_reference_xy.gcode` contains only the deposited outline's XY endpoints,
plus G21/G90 and a schematic G0 to its start. It excludes extrusion, Z, heating,
fans, startup travel and feed rates. This is an inspection artifact, not a
machine-ready program. Actual motion still needs safe positioning and feed rate.

The source is the supplied Ultimaker S3 slicer file, not camera reconstruction.
The original coordinates are retained: X=100.370..129.628 mm and
Y=80.370..109.628 mm. The path closes at its initial point and has length
116.56367 mm. Small corner segments are preserved rather than replacing them
with an ideal square. Extrusion increases are used only to select print moves.

- `square_reference_xy.csv`: endpoints with source-line provenance.
- `square_reference_xy.svg`: intended printer-plane path.
- `side_observed_pixels.svg`: trajectory in the previously recorded side video.
- `path_summary.json`: bounds, length, capture coverage and source filenames.

The side recording is not verified to be the same square print. Its full path
is plotted without selecting a perimeter pass or registering time. Camera pixels
cannot be compared directly to bed millimeters. Current visually observed data
includes image position, direction and trajectory; timestamps/tracking status
are also logged. Side-plane millimeters are available only after reference-plane
calibration and remain different from printer XY. Temperature, filament feed,
extrusion E, and machine Z are not recovered here.

To extract another reference, from `capture`:

```bash
python legacy/extract_print_path.py /path/to/model.gcode --output-dir references/another_print
```

Optionally add `--side-csv captures/side_camera/date/session/side_trajectory.csv`.
Use a new output directory each run. The utility supports linear moves with
absolute XYZ and absolute/relative E, and assumes millimeter input. It is not
a complete arbitrary G-code interpreter. Generated basenames retain "square"
for this experiment.
