import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from application.gui import export_xy
from application.collect_side_xy_calibration import StableStopCollector, _frame_timestamp
from application.side_xy_calibration_gcode import (
    CALIBRATION_POINTS,
    prepare_calibration_copy,
)
from cameras.side import printer_xy_tracker
from cameras.side.printer_xy_calibration import SideXYMapper, fit_side_xy_calibration
from cameras.top.nozzle_grid_tracker import main as run_upward_tracker


class GuiExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "metadata.json").write_text(json.dumps({
            "processing_status": "complete",
            "bed_xy_commands_available": True,
        }))
        (self.run / "reconstructed_path_only.gcode").write_text(
            "; ANALYSIS_ONLY:YES\nG0 X10.000 Y20.000\nG1 X11.000 Y20.000\n"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_exports_calibrated_xy_path_to_chosen_file(self):
        destination = self.root / "chosen.gcode"
        self.assertEqual(export_xy(self.run, destination), destination.resolve())
        self.assertEqual(
            destination.read_text(),
            (self.run / "reconstructed_path_only.gcode").read_text(),
        )

    def test_rejects_unavailable_xy_without_creating_output(self):
        metadata = json.loads((self.run / "metadata.json").read_text())
        metadata["bed_xy_commands_available"] = False
        (self.run / "metadata.json").write_text(json.dumps(metadata))
        destination = self.root / "unavailable.gcode"
        with self.assertRaisesRegex(ValueError, "Calibrated bed XY"):
            export_xy(self.run, destination)
        self.assertFalse(destination.exists())

    def test_rejects_output_inside_run_folder(self):
        source = self.run / "reconstructed_path_only.gcode"
        with self.assertRaisesRegex(ValueError, "destination outside"):
            export_xy(self.run, source)
        reference = self.run / "original_reference.gcode"
        with self.assertRaisesRegex(ValueError, "destination outside"):
            export_xy(self.run, reference)
        with self.assertRaisesRegex(ValueError, "destination outside"):
            export_xy(self.run, self.run / "other.gcode")

    def test_rejects_path_without_xy_commands(self):
        (self.run / "reconstructed_path_only.gcode").write_text("; NO_XY_COMMANDS\n")
        with self.assertRaisesRegex(ValueError, "no supported XY"):
            export_xy(self.run, self.root / "empty.gcode")

    def test_upward_tracker_stops_cleanly_when_gui_requests_it(self):
        video = self.root / "camera.mp4"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (320, 240))
        self.assertTrue(writer.isOpened())
        for value in range(4):
            writer.write(np.full((240, 320, 3), value + 100, dtype=np.uint8))
        writer.release()

        runs = self.root / "runs"
        stop_file = self.root / "stop"

        def request_stop(delay):
            self.assertGreaterEqual(delay, 0)
            stop_file.touch()
            return -1

        with patch.object(cv2, "namedWindow"), patch.object(cv2, "imshow"), \
                patch.object(cv2, "destroyAllWindows"), patch.object(cv2, "waitKey", side_effect=request_stop):
            run_upward_tracker([
                "--input", str(video), "--runs-dir", str(runs),
                "--no-reference-prompt", "--stop-file", str(stop_file),
            ])
        self.assertTrue(stop_file.is_file())
        self.assertEqual(list(runs.iterdir()), [])

    def test_side_calibration_fits_and_maps_held_out_printer_positions(self):
        samples = self.root / "samples.csv"
        rows = ["x_mm,y_mm,nozzle_x_px,head_width_px,split"]
        positions = [
            (100, 20, "train"), (100, 60, "train"), (100, 100, "train"),
            (200, 20, "train"), (200, 60, "train"), (200, 100, "train"),
            (300, 20, "train"), (300, 60, "train"), (300, 100, "train"),
            (150, 40, "validation"), (250, 80, "validation"),
        ]
        for x_mm, y_mm, split in positions:
            width = 10000 / (x_mm + 100)
            nozzle_x = width * (y_mm - 200 / width - 10) / 3
            rows.append(f"{x_mm},{y_mm},{nozzle_x},{width},{split}")
        samples.write_text("\n".join(rows) + "\n")

        model = fit_side_xy_calibration(samples, depth_axis="X")
        mapper = SideXYMapper(model)
        validation = positions[-1]
        x_mm, y_mm, _ = validation
        width = 10000 / (x_mm + 100)
        nozzle_x = width * (y_mm - 200 / width - 10) / 3
        estimated = mapper.estimate(width, nozzle_x)
        self.assertAlmostEqual(estimated[0], x_mm, places=6)
        self.assertAlmostEqual(estimated[1], y_mm, places=6)
        self.assertLess(model["validation_rmse_mm"]["x"], 1e-8)
        self.assertLess(model["validation_rmse_mm"]["y"], 1e-8)

    def test_side_calibration_requires_separate_held_out_positions(self):
        samples = self.root / "samples.csv"
        samples.write_text(
            "x_mm,y_mm,nozzle_x_px,head_width_px,split\n"
            "100,20,20,40,train\n200,40,30,30,train\n300,60,40,20,train\n"
            "100,20,21,40,validation\n200,40,31,30,validation\n"
        )
        with self.assertRaisesRegex(ValueError, "separate"):
            fit_side_xy_calibration(samples, depth_axis="X")

    def test_prepares_calibration_gcode_copy_without_changing_source(self):
        source = self.root / "circle.gcode"
        original = (
            ";PRINT.TIME:12\n"
            "G280 S1\n"
            "G0 Z20.001\n"
            "G1 F2700 E-6.5\n"
            ";LAYER_COUNT:1\n"
            "G0 F9000 X180 Y180\n"
            ";End of Gcode\n"
        )
        source.write_text(original)
        destination = self.root / "circle_calibration.gcode"

        result = prepare_calibration_copy(source, destination)

        self.assertEqual(result, destination.resolve())
        self.assertEqual(source.read_text(), original)
        prepared = destination.read_text()
        self.assertLess(prepared.index("G1 F2700 E-6.5"), prepared.index(";SIDE_XY_CALIBRATION_START"))
        self.assertLess(prepared.index(";SIDE_XY_CALIBRATION_END"), prepared.index(";LAYER_COUNT:1"))
        self.assertLess(prepared.index(";LAYER_COUNT:1"), prepared.index("G0 F9000 X180 Y180"))
        self.assertEqual(prepared.count("M400"), len(CALIBRATION_POINTS))
        self.assertEqual(prepared.count("G4 P5000"), len(CALIBRATION_POINTS))
        self.assertIn(";SIDE_XY_CALIBRATION_POINT:validation,G,140,120", prepared)
        self.assertIn(";PRINT.TIME:12", prepared)

    def test_calibration_gcode_generator_rejects_source_overwrite(self):
        source = self.root / "circle.gcode"
        source.write_text("G1 F2700 E-6.5\n;LAYER_COUNT:1\n")
        with self.assertRaisesRegex(ValueError, "destination copy"):
            prepare_calibration_copy(source, source)

    def test_side_calibration_collector_waits_for_motion_then_samples_each_dwell_once(self):
        collector = StableStopCollector(
            points=CALIBRATION_POINTS[:2], settle_seconds=0.5,
        )
        collector.arm()
        for frame in range(6):
            self.assertIsNone(collector.update(frame / 10, (10, 10), 20, 40))

        for frame, x in enumerate((20, 30, 40), start=6):
            collector.update(frame / 10, (x, 10), x + 5, 40)
        first_sample = None
        for frame in range(9, 15):
            first_sample = collector.update(frame / 10, (40, 10), 45, 40) or first_sample
        self.assertEqual(first_sample["label"], "A")
        self.assertEqual(first_sample["split"], "train")
        self.assertEqual(first_sample["x_mm"], 80)

        for frame, x in enumerate((60, 80, 100), start=15):
            collector.update(frame / 10, (x, 10), x + 5, 40)
        second_sample = None
        for frame in range(18, 28):
            second_sample = collector.update(frame / 10, (100, 10), 105, 40) or second_sample
        self.assertEqual(second_sample["label"], "B")
        self.assertEqual(second_sample["x_mm"], 160)
        self.assertEqual(len(collector.samples), 2)

    def test_side_calibration_collector_detects_gradual_motion(self):
        collector = StableStopCollector(
            points=CALIBRATION_POINTS[:1], settle_seconds=0.5,
        )
        collector.arm()
        for frame in range(5):
            collector.update(frame / 10, (10, 10), 20, 40)
        for frame, x in enumerate((11, 12, 14, 16, 18), start=5):
            collector.update(frame / 10, (x, 10), x + 5, 40)
        sample = None
        for frame in range(10, 18):
            sample = collector.update(frame / 10, (18, 10), 23, 40) or sample
        self.assertIsNotNone(sample)
        self.assertEqual(sample["label"], "A")

    def test_prerecorded_calibration_uses_video_timestamps(self):
        self.assertAlmostEqual(_frame_timestamp(True, 45, 30.0), 1.5)
        with patch("application.collect_side_xy_calibration.time.monotonic", return_value=123.5):
            self.assertEqual(_frame_timestamp(False, 45, 30.0), 123.5)

    def test_side_yolo_capture_reconstructs_calibrated_printer_xy(self):
        video = self.root / "side-camera.mp4"
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10, (180, 120))
        self.assertTrue(writer.isOpened())
        for value in range(4):
            writer.write(np.full((120, 180, 3), 100 + value, dtype=np.uint8))
        writer.release()

        model = self.root / "printer-head.pt"
        model.write_bytes(b"mock weights")
        calibration = self.root / "side-calibration.json"
        calibration.write_text(json.dumps({
            "version": 1,
            "coordinate_space": "bed_mm",
            "coordinate_model": "calibrated_side_xy",
            "depth_axis": "X",
            "horizontal_axis": "Y",
            "depth_coefficients_inverse_width_intercept": [2000, 0],
            "horizontal_coefficients_nozzle_x_over_width_inverse_width_intercept": [100, 0, 0],
            "training_bounds_mm": {"x": [0, 100], "y": [0, 200]},
        }))
        detections = lambda frame: [((40, 35, min(40, frame.shape[1] - 40), 20), .95)]

        def select_head_and_tip(window, callback):
            self.assertTrue(window)
            callback(cv2.EVENT_LBUTTONDOWN, 60, 45, 0, None)
            callback(cv2.EVENT_LBUTTONDOWN, 60, 75, cv2.EVENT_FLAG_SHIFTKEY, None)

        with patch.object(printer_xy_tracker, "ModelProposer", return_value=detections), \
                patch.object(cv2, "namedWindow"), \
                patch.object(cv2, "setMouseCallback", side_effect=select_head_and_tip), \
                patch.object(cv2, "imshow"), patch.object(cv2, "destroyAllWindows"), \
                patch.object(cv2, "getWindowProperty", return_value=1), \
                patch.object(cv2, "waitKey", side_effect=[-1, ord("v"), ord("q")]):
            printer_xy_tracker.main([
                "--input", str(video), "--model", str(model),
                "--xy-calibration", str(calibration),
                "--runs-dir", str(self.root / "side-runs"),
            ])

        run_folders = list((self.root / "side-runs").iterdir())
        self.assertEqual(len(run_folders), 1)
        run_folder = run_folders[0]
        metadata = json.loads((run_folder / "metadata.json").read_text())
        self.assertTrue(metadata["bed_xy_commands_available"])
        self.assertEqual(metadata["coordinate_space"], "bed_mm")
        gcode = (run_folder / "reconstructed_path_only.gcode").read_text()
        self.assertIn("G0 X50.000000 Y150.000000", gcode)
        with (run_folder / "trajectory.csv").open(newline="") as trajectory:
            row = next(csv.DictReader(trajectory))
        self.assertEqual(row["coordinate_source"], "calibrated_side_xy")


if __name__ == "__main__":
    unittest.main()
