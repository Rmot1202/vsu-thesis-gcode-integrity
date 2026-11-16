"""Tests for backend.integrity using a synthetic pinhole camera (no video/YOLO needed)."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.integrity.gcode_timeline import load_timeline
from backend.integrity import mapping
from backend.integrity.compare import calibrate, calibrate_multi, compare

HEADER = """;FLAVOR:Marlin
;LAYER_COUNT:1
;LAYER:0
M204 S1500
G0 F6000 X{x0} Y{y0} Z0.3
"""


def write_gcode(path, pts, feed=1800):
    lines = [HEADER.format(x0=pts[0][0], y0=pts[0][1])]
    e = 0.0
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        e += np.hypot(x1 - x0, y1 - y0) * 0.03
        lines.append(f"G1 F{feed} X{x1} Y{y1} E{e:.4f}\n")
    lines.append("G0 F6000 X10 Y10\n")
    Path(path).write_text("".join(lines))


def fake_camera(xy):
    """Front camera: X is image-horizontal, Y is depth (Y=0 nearest)."""
    depth = 300.0 + xy[:, 1]
    f = 900.0
    px = 810.0 + f * (xy[:, 0] - 115.0) / depth
    py = 300.0 + f * 40.0 / depth
    w = f * 120.0 / depth
    return px, py, w, w * 0.4


def write_track(path, gcode, start_s=5.0, fps=30.0, pad_s=3.0, noise=0.0, seed=0):
    tl = load_timeline(gcode)
    t = np.arange(0, start_s + tl.duration + pad_s, 1.0 / fps)
    tau = np.clip(t - start_s, 0, tl.duration)
    xy, _ = tl.sample(tau)
    px, py, w, h = fake_camera(xy)
    rng = np.random.default_rng(seed)
    px, w = px + rng.normal(0, noise, len(t)), w + rng.normal(0, noise * 0.3, len(t))
    with open(path, "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["frame_index", "time_s", "head_cx_px", "head_cy_px", "head_w_px", "head_h_px", "confidence", "valid"])
        for i in range(len(t)):
            wr.writerow([i, f"{t[i]:.4f}", px[i], py[i], w[i], h[i], 0.9, 1])


RECT = [(40, 20), (190, 20), (190, 170), (40, 170), (40, 20), (190, 20)]
RECT2 = [(30, 15), (200, 15), (200, 180), (30, 180), (30, 15)]
ZIG = [(40, 40), (190, 40), (190, 80), (40, 80), (40, 120), (190, 120), (190, 160), (40, 160)]


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        write_gcode(self.tmp / "rect.gcode", RECT)
        write_gcode(self.tmp / "zig.gcode", ZIG)
        write_track(self.tmp / "rect.csv", self.tmp / "rect.gcode", noise=0.5)
        write_track(self.tmp / "zig.csv", self.tmp / "zig.gcode", start_s=7.0, noise=0.5, seed=1)

    def test_timeline_reaches_corners(self):
        tl = load_timeline(self.tmp / "rect.gcode")
        self.assertGreater(tl.duration, 1.0)
        np.testing.assert_allclose(tl.sample([tl.duration])[0][0], RECT[-1], atol=1e-6)

    def test_calibration_recovers_start_and_accuracy(self):
        cal = calibrate(self.tmp / "rect.csv", self.tmp / "rect.gcode", self.tmp / "cal.json")
        self.assertAlmostEqual(cal["print_start_video_s"], 5.0, delta=0.15)
        self.assertLess(cal["holdout_rmse_mm"], 2.0)
        self.assertIn("x_coefficients", json.loads((self.tmp / "cal.json").read_text()))

    def test_correct_gcode_passes_and_wrong_gcode_fails(self):
        calibrate(self.tmp / "rect.csv", self.tmp / "rect.gcode", self.tmp / "cal.json")
        good = compare(self.tmp / "zig.csv", self.tmp / "zig.gcode", self.tmp / "cal.json", self.tmp / "good")
        bad = compare(self.tmp / "zig.csv", self.tmp / "rect.gcode", self.tmp / "cal.json", None, make_figures=False)
        self.assertTrue(good["verdict"].startswith("CONSISTENT"), good)
        self.assertTrue(bad["verdict"].startswith("DEVIATES"), bad)
        self.assertTrue((self.tmp / "good" / "side_by_side.png").exists())
        self.assertTrue((self.tmp / "good" / "observed_bed_path.csv").exists())


    def test_poly_design_shapes(self):
        obs = {k: np.linspace(280, 400, 20) for k in ("px", "py", "w", "h")}
        self.assertEqual(mapping.design(obs, "poly2")[0].shape, (20, 15))
        self.assertEqual(mapping.design(obs, "poly3")[0].shape, (20, 35))

    def test_extrapolation_mask(self):
        obs = {k: np.array([300.0, 350.0, 900.0]) for k in ("px", "py", "w", "h")}
        bounds = {k: [290.0, 360.0] for k in ("px", "py", "w", "h")}
        np.testing.assert_array_equal(mapping.extrapolated(obs, bounds), [False, False, True])
        self.assertFalse(mapping.extrapolated(obs, None).any())

    def _multi(self, model="poly3"):
        write_gcode(self.tmp / "rect2.gcode", RECT2)
        write_track(self.tmp / "rect2.csv", self.tmp / "rect2.gcode", start_s=4.0, noise=0.5, seed=2)
        pairs = [(self.tmp / "rect.csv", self.tmp / "rect.gcode"),
                 (self.tmp / "rect2.csv", self.tmp / "rect2.gcode"),
                 (self.tmp / "zig.csv", self.tmp / "zig.gcode")]
        return calibrate_multi(pairs, self.tmp / "multi.json", model=model)

    def test_multi_calibration_recovers_starts_and_lovo(self):
        cal = self._multi()
        starts = [p["print_start_video_s"] for p in cal["calibration_prints"]]
        np.testing.assert_allclose(starts, [5.0, 4.0, 7.0], atol=0.15)
        self.assertEqual(cal["mapping_model"], "poly3")
        self.assertLess(cal["holdout_rmse_mm"], 2.0)
        self.assertLessEqual(cal["fit_rmse_mm"], cal["holdout_rmse_mm"] + 1e-9)
        self.assertIn("feature_bounds", json.loads((self.tmp / "multi.json").read_text()))
        for p in cal["calibration_prints"]:
            self.assertGreater(p["lovo_within_5mm_pct"], 95)

    def test_multi_calibration_compare_and_extrapolation(self):
        self._multi()
        good = compare(self.tmp / "zig.csv", self.tmp / "zig.gcode", self.tmp / "multi.json", None, make_figures=False)
        bad = compare(self.tmp / "zig.csv", self.tmp / "rect.gcode", self.tmp / "multi.json", None, make_figures=False)
        self.assertTrue(good["verdict"].startswith("CONSISTENT"), good)
        self.assertTrue(bad["verdict"].startswith("DEVIATES"), bad)
        self.assertLess(good["extrapolated_fraction"], 0.05)


if __name__ == "__main__":
    unittest.main()
