#!/usr/bin/env python3
"""Fit and apply a calibrated side-camera-to-printer-XY mapping."""
import argparse
import csv
import json
from pathlib import Path

import numpy as np


SAMPLE_FIELDS = {"x_mm", "y_mm", "nozzle_x_px", "head_width_px", "split"}


def _features(rows, depth_axis):
    width = np.asarray([float(row["head_width_px"]) for row in rows], dtype=float)
    pixel_x = np.asarray([float(row["nozzle_x_px"]) for row in rows], dtype=float)
    if not np.isfinite(width).all() or (width <= 0).any() or not np.isfinite(pixel_x).all():
        raise ValueError("Calibration head widths must be positive and all image coordinates finite.")
    inverse_width = 1.0 / width
    depth_features = np.column_stack((inverse_width, np.ones(len(rows))))
    horizontal_features = np.column_stack((pixel_x * inverse_width, inverse_width, np.ones(len(rows))))
    depth_values = np.asarray([float(row[f"{depth_axis.lower()}_mm"]) for row in rows], dtype=float)
    horizontal_axis = "Y" if depth_axis == "X" else "X"
    horizontal_values = np.asarray([float(row[f"{horizontal_axis.lower()}_mm"]) for row in rows], dtype=float)
    return depth_features, depth_values, horizontal_features, horizontal_values


def fit_side_xy_calibration(samples_path, depth_axis="X"):
    """Fit inverse-width depth and perspective-corrected horizontal mappings.

    CSV columns: x_mm,y_mm,nozzle_x_px,head_width_px,split. Use split=train or
    split=validation; validation positions must not occur in the training set.
    """
    depth_axis = depth_axis.upper()
    if depth_axis not in {"X", "Y"}:
        raise ValueError("depth_axis must be X or Y.")

    with Path(samples_path).expanduser().open(newline="") as sample_file:
        reader = csv.DictReader(sample_file)
        if not SAMPLE_FIELDS <= set(reader.fieldnames or ()):
            raise ValueError(f"Calibration CSV must contain: {', '.join(sorted(SAMPLE_FIELDS))}")
        rows = list(reader)
    if not rows:
        raise ValueError("Calibration CSV has no samples.")
    if any(row["split"] not in {"train", "validation"} for row in rows):
        raise ValueError("Every calibration row must be marked split=train or split=validation.")
    training = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    if not training or not validation:
        raise ValueError("Calibration requires training and held-out validation samples.")

    for row in rows:
        try:
            values = [float(row[key]) for key in ("x_mm", "y_mm", "nozzle_x_px", "head_width_px")]
        except (TypeError, ValueError) as error:
            raise ValueError("Calibration measurements must be numeric.") from error
        if not np.isfinite(values).all():
            raise ValueError("Calibration measurements must be finite.")
        if values[3] <= 0:
            raise ValueError("Calibration head widths must be positive.")

    train_positions = {(float(row["x_mm"]), float(row["y_mm"])) for row in training}
    validation_positions = {(float(row["x_mm"]), float(row["y_mm"])) for row in validation}
    if len(train_positions) < 3:
        raise ValueError("Collect training samples at three or more distinct bed positions.")
    if len(validation_positions) < 2:
        raise ValueError("Collect held-out validation samples at two or more distinct bed positions.")
    if train_positions & validation_positions:
        raise ValueError("Validation positions must be separate from all training positions.")

    depth_x, depth_y, horizontal_x, horizontal_y = _features(training, depth_axis)
    if np.linalg.matrix_rank(depth_x) < 2 or np.linalg.matrix_rank(horizontal_x) < 3:
        raise ValueError("Training samples do not span enough head sizes and horizontal positions.")
    depth_coefficients = np.linalg.lstsq(depth_x, depth_y, rcond=None)[0]
    horizontal_coefficients = np.linalg.lstsq(horizontal_x, horizontal_y, rcond=None)[0]

    validation_depth_x, validation_depth_y, validation_horizontal_x, validation_horizontal_y = _features(validation, depth_axis)
    depth_errors = validation_depth_x @ depth_coefficients - validation_depth_y
    horizontal_errors = validation_horizontal_x @ horizontal_coefficients - validation_horizontal_y
    validation_rmse = {
        depth_axis.lower(): float(np.sqrt(np.mean(np.square(depth_errors)))),
        ("y" if depth_axis == "X" else "x"): float(np.sqrt(np.mean(np.square(horizontal_errors)))),
    }

    return {
        "version": 1,
        "coordinate_space": "bed_mm",
        "coordinate_model": "calibrated_side_xy",
        "method": "side_camera_inverse_head_width_and_perspective_corrected_nozzle_x",
        "depth_axis": depth_axis,
        "horizontal_axis": "Y" if depth_axis == "X" else "X",
        "depth_coefficients_inverse_width_intercept": depth_coefficients.tolist(),
        "horizontal_coefficients_nozzle_x_over_width_inverse_width_intercept": horizontal_coefficients.tolist(),
        "training_bounds_mm": {
            "x": [min(point[0] for point in train_positions), max(point[0] for point in train_positions)],
            "y": [min(point[1] for point in train_positions), max(point[1] for point in train_positions)],
        },
        "training_positions": len(train_positions),
        "training_samples": len(training),
        "validation_positions": len(validation_positions),
        "validation_samples": len(validation),
        "validation_rmse_mm": validation_rmse,
        "validation_note": "Held-out positions are measured independently; inspect RMSE before using the mapping.",
    }


class SideXYMapper:
    def __init__(self, calibration):
        if isinstance(calibration, (str, Path)):
            calibration = json.loads(Path(calibration).expanduser().read_text())
        self.calibration = calibration
        if calibration.get("version") != 1 or calibration.get("coordinate_space") != "bed_mm":
            raise ValueError("Unsupported or non-bed side XY calibration.")
        if calibration.get("depth_axis") not in {"X", "Y"}:
            raise ValueError("Calibration depth_axis must be X or Y.")
        self.depth_axis = calibration["depth_axis"]
        self.horizontal_axis = calibration.get("horizontal_axis")
        if self.horizontal_axis != ("Y" if self.depth_axis == "X" else "X"):
            raise ValueError("Calibration horizontal_axis must be the other printer axis.")
        self.depth_coefficients = np.asarray(
            calibration.get("depth_coefficients_inverse_width_intercept"), dtype=float
        )
        self.horizontal_coefficients = np.asarray(
            calibration.get("horizontal_coefficients_nozzle_x_over_width_inverse_width_intercept"),
            dtype=float,
        )
        if (self.depth_coefficients.shape != (2,) or self.horizontal_coefficients.shape != (3,)
                or not np.isfinite(self.depth_coefficients).all()
                or not np.isfinite(self.horizontal_coefficients).all()):
            raise ValueError("Side XY calibration coefficients are invalid.")
        bounds = calibration.get("training_bounds_mm", {})
        self.bounds = {axis: np.asarray(bounds.get(axis), dtype=float) for axis in ("x", "y")}
        if any(value.shape != (2,) or not np.isfinite(value).all() or value[0] > value[1]
               for value in self.bounds.values()):
            raise ValueError("Side XY calibration position bounds are invalid.")

    def estimate(self, head_width_px, nozzle_x_px):
        if not np.isfinite([head_width_px, nozzle_x_px]).all() or head_width_px <= 0:
            return None
        inverse_width = 1.0 / head_width_px
        depth = float(np.array([inverse_width, 1.0]) @ self.depth_coefficients)
        horizontal = float(np.array([nozzle_x_px * inverse_width, inverse_width, 1.0])
                           @ self.horizontal_coefficients)
        result = {self.depth_axis.lower(): depth, self.horizontal_axis.lower(): horizontal}
        for axis, value in result.items():
            low, high = self.bounds[axis]
            margin = max(2.0, (high - low) * 0.05)
            if not low - margin <= value <= high + margin:
                return None
        return result["x"], result["y"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, required=True, help="Known-position CSV with train/validation split")
    parser.add_argument("--output", type=Path, required=True, help="Destination JSON calibration file")
    parser.add_argument("--depth-axis", choices=("X", "Y"), default="X",
                        help="Printer axis toward/away from the side camera")
    args = parser.parse_args(argv)
    calibration = fit_side_xy_calibration(args.samples, args.depth_axis)
    args.output.expanduser().write_text(json.dumps(calibration, indent=2) + "\n")
    print(json.dumps(calibration, indent=2))


if __name__ == "__main__":
    main()
