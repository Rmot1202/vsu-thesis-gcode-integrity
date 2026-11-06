#!/usr/bin/env python3
"""Prepare a separate G-code copy with labeled side-camera XY calibration moves."""
import argparse
from pathlib import Path


CALIBRATION_POINTS = (
    ("train", "A", 80, 60),
    ("train", "B", 160, 60),
    ("train", "C", 80, 140),
    ("train", "D", 160, 140),
    ("train", "E", 120, 100),
    ("validation", "F", 100, 80),
    ("validation", "G", 140, 120),
)
START_MARKER = ";SIDE_XY_CALIBRATION_START"
END_MARKER = ";SIDE_XY_CALIBRATION_END"


def calibration_block(dwell_ms=5000, feedrate=1200):
    if not isinstance(dwell_ms, int) or dwell_ms <= 0:
        raise ValueError("dwell_ms must be a positive integer.")
    if not isinstance(feedrate, int) or feedrate <= 0:
        raise ValueError("feedrate must be a positive integer.")

    lines = [
        "",
        "; ===== SIDE-CAMERA CALIBRATION =====",
        "; Supervise the printer; verify clearance and reachability before running.",
        "; No extrusion is commanded in this block.",
        START_MARKER,
    ]
    for split, label, x_mm, y_mm in CALIBRATION_POINTS:
        lines.extend((
            f";SIDE_XY_CALIBRATION_POINT:{split},{label},{x_mm},{y_mm}",
            f"G0 F{feedrate} X{x_mm} Y{y_mm}",
            "M400",
            f"G4 P{dwell_ms}",
        ))
    lines.extend((END_MARKER, "; ===== END SIDE-CAMERA CALIBRATION =====", ""))
    return "\n".join(lines)


def prepare_calibration_copy(source, destination, dwell_ms=5000, feedrate=1200):
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if source == destination:
        raise ValueError("Choose a destination copy; the source G-code will not be modified.")
    if not source.is_file():
        raise ValueError(f"Source G-code does not exist: {source}")
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite an existing G-code file: {destination}")

    with source.open(newline="") as source_file:
        text = source_file.read()
    newline = "\r\n" if "\r\n" in text else "\n"
    if START_MARKER in text or END_MARKER in text:
        raise ValueError("Source already contains a side XY calibration block.")

    layer_marker = ";LAYER_COUNT:1"
    clearance_move = "G1 F2700 E-6.5"
    if text.count(layer_marker) != 1 or text.count(clearance_move) != 1:
        raise ValueError(
            "Expected exactly one `G1 F2700 E-6.5` and one `;LAYER_COUNT:1` insertion anchor."
        )
    clearance_end = text.index(clearance_move) + len(clearance_move)
    layer_start = text.index(layer_marker)
    if clearance_end > layer_start:
        raise ValueError("The extrusion-retract move must appear before the layer marker.")
    block = calibration_block(dwell_ms=dwell_ms, feedrate=feedrate).replace("\n", newline)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="") as output_file:
        output_file.write(text[:clearance_end] + block + text[layer_start:])
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Original G-code; it is never modified")
    parser.add_argument("--output", type=Path, required=True, help="New calibration-enabled copy")
    parser.add_argument("--dwell-ms", type=int, default=5000, help="Pause at each known XY position")
    parser.add_argument("--feedrate", type=int, default=1200, help="Calibration travel feedrate in mm/min")
    args = parser.parse_args(argv)
    output = prepare_calibration_copy(args.input, args.output, args.dwell_ms, args.feedrate)
    print(f"Calibration copy created: {output}")
    print("Review the file and verify printer clearance before supervised use.")


if __name__ == "__main__":
    main()
