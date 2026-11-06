"""Front/side camera -> printer bed XY mapping and its fitting.

Camera geometry (pinhole, camera looking roughly along the printer Y axis,
mounted a little above the head plane):

* apparent head size (box width ``w`` and height ``h``) is inversely
  proportional to the distance from the camera -> depth axis ``Y``;
* the horizontal image coordinate is perspective-scaled by the same distance
  -> ``X ~ c1 * px / w + c2 / w + c3``;
* because the camera looks slightly down, the head also rises in the image as
  it moves away (``py``), which is a second, independent depth cue.

All models below are linear in their coefficients, so fitting is ordinary
least squares.  ``pinhole`` is the original two-equation model used by
``cameras/side/printer_xy_calibration.py``; ``pinhole_plus`` (default) adds
the height, image-y and lens-curvature terms, which cut the held-out Y error
roughly in half on the 10-8-26 recordings.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

MODELS = ("pinhole", "pinhole_yaw", "pinhole_plus")


def design(obs, model="pinhole_plus", cx0=None):
    px, py, w, h = (np.asarray(obs[k], float) for k in ("px", "py", "w", "h"))
    v = 1.0 / w
    u = px * v
    one = np.ones_like(v)
    if model == "pinhole":
        return np.column_stack((u, v, one)), np.column_stack((v, one))
    if model == "pinhole_yaw":
        return np.column_stack((u, v, one)), np.column_stack((v, one, u))
    if model == "pinhole_plus":
        c = (px - (cx0 if cx0 is not None else 810.0)) * v
        return (np.column_stack((u, v, one, c * c)),
                np.column_stack((v, 1.0 / h, one, u, c * c, py, py * v)))
    raise ValueError(f"Unknown mapping model {model}; choose from {MODELS}")


def fit(obs, target_xy, model="pinhole_plus", cx0=810.0):
    hx, hy = design(obs, model, cx0)
    cx = np.linalg.lstsq(hx, target_xy[:, 0], rcond=None)[0]
    cy = np.linalg.lstsq(hy, target_xy[:, 1], rcond=None)[0]
    return cx, cy


def apply(cx, cy, obs, model="pinhole_plus", cx0=810.0):
    hx, hy = design(obs, model, cx0)
    return np.column_stack((hx @ cx, hy @ cy))


def subset(obs, sel):
    return {k: np.asarray(v)[sel] for k, v in obs.items()}


class BedMapper:
    """Load a calibration JSON and map tracked head observations to bed mm."""

    def __init__(self, calibration):
        if isinstance(calibration, (str, Path)):
            calibration = json.loads(Path(calibration).read_text())
        if calibration.get("coordinate_space") != "bed_mm":
            raise ValueError("Calibration is not in bed_mm")
        self.cal = calibration
        self.model = calibration.get("mapping_model", "pinhole")
        self.cx0 = calibration.get("image_center_x_px", 810.0)
        self.cx = np.asarray(calibration["x_coefficients"], float)
        self.cy = np.asarray(calibration["y_coefficients"], float)

    def __call__(self, obs):
        return apply(self.cx, self.cy, obs, self.model, self.cx0)


def to_json(cx, cy, model, cx0, extra):
    cal = {
        "version": 2,
        "coordinate_space": "bed_mm",
        "coordinate_model": "calibrated_side_xy",
        "mapping_model": model,
        "image_center_x_px": cx0,
        "method": "head-box pinhole mapping fitted against a G-code-timed calibration print",
        "x_coefficients": list(map(float, cx)),
        "y_coefficients": list(map(float, cy)),
        "depth_axis": "Y",
        "horizontal_axis": "X",
    }
    if model == "pinhole":  # also loadable by cameras/side/printer_xy_calibration.SideXYMapper
        cal["depth_coefficients_inverse_width_intercept"] = list(map(float, cy))
        cal["horizontal_coefficients_nozzle_x_over_width_inverse_width_intercept"] = list(map(float, cx))
    cal.update(extra)
    return cal
