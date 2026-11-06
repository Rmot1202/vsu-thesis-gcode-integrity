import sys
import csv
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from backend.post_analysis.printer_head import PrinterHeadTracker, process_video


class PrinterHeadTests(unittest.TestCase):
    def test_calibrates_and_tracks_label_offset(self):
        tracker = PrinterHeadTracker(proposer=lambda frame: [((30, 30, 40, 18), .9), ((130, 30, 30, 18), .8)])
        frame = np.zeros((120, 180, 3), np.uint8)
        cv2.rectangle(frame, (30, 30), (70, 48), (0, 255, 255), -1)
        cv2.rectangle(frame, (130, 30), (160, 48), (0, 255, 255), -1)
        tip = np.array([50.0, 75.0])
        self.assertTrue(tracker.calibrate(frame, (50, 39), tip))

        moved = np.zeros_like(frame)
        cv2.rectangle(moved, (40, 35), (80, 53), (0, 255, 255), -1)
        cv2.rectangle(moved, (130, 30), (160, 48), (0, 255, 255), -1)
        tracker.proposer = lambda frame: [((40, 35, 40, 18), .9), ((130, 30, 30, 18), .8)]
        marker, estimated_tip = tracker.update(moved)
        np.testing.assert_allclose(marker, [60.0, 44.0])
        np.testing.assert_allclose(estimated_tip, [60.0, 80.0])

    def test_pixel_width_estimates_depth_and_scales_tip(self):
        frame = np.zeros((120, 180, 3), np.uint8)
        tracker = PrinterHeadTracker(
            proposer=lambda frame: [((30, 30, 40, 20), .9)], initial_distance_mm=500)
        self.assertTrue(tracker.calibrate(frame, (50, 40), (50, 70)))
        np.testing.assert_allclose(tracker.depth_measurement(), [40, 20, 1, 1, 500, 0])
        tracker.proposer = lambda frame: [((25, 25, 50, 25), .9)]
        center, tip = tracker.update(frame)
        np.testing.assert_allclose(tip - center, [0, 37.5])
        np.testing.assert_allclose(tracker.depth_measurement(), [50, 25, 1.25, .8, 400, 100])
        tracker.proposer = lambda frame: [((35, 35, 32, 16), .9)]
        tracker.update(frame)
        np.testing.assert_allclose(tracker.depth_measurement(), [32, 16, .8, 1.25, 625, -125])
        tracker.proposer = lambda frame: []
        tracker.update(frame)
        self.assertEqual(tracker.depth_measurement(), ('',) * 6)

    def test_relative_depth_without_distance_and_invalid_calibration(self):
        frame = np.zeros((120, 180, 3), np.uint8)
        tracker = PrinterHeadTracker(proposer=lambda frame: [((30, 30, 40, 20), .9)])
        self.assertTrue(tracker.calibrate(frame, (50, 40), (50, 70)))
        self.assertEqual(tracker.depth_measurement(), (40, 20, 1, 1, '', ''))
        for distance in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                PrinterHeadTracker(proposer=lambda frame: [], initial_distance_mm=distance)
        tracker = PrinterHeadTracker(proposer=lambda frame: [((30, 30, 0, 20), .9)])
        self.assertFalse(tracker.calibrate(frame, (50, 40), (50, 70)))

    def test_reports_no_head_detection(self):
        tracker = PrinterHeadTracker(proposer=lambda frame: [])
        self.assertFalse(tracker.calibrate(np.zeros((80, 80, 3), np.uint8), (20, 20), (30, 30)))
        self.assertIn('No printer head detected', tracker.error)

    def test_process_video_writes_separate_analysis_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'raw_video.mp4'
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'mp4v'), 10, (180, 120))
            self.assertTrue(writer.isOpened())
            for frame_index in range(4):
                frame = np.zeros((120, 180, 3), np.uint8)
                x = 30 + frame_index * 2
                cv2.rectangle(frame, (x, 30), (x + 40, 48), (0, 255, 255), -1)
                writer.write(frame)
            writer.release()
            trajectory = root / 'trajectory.csv'
            trajectory.write_text('existing live trajectory\n')

            predictions = iter([[((30 + i * 2, 30, 40, 18), .9)] for i in range(4)])
            result = process_video(source, marker_click=(50, 39),
                                   nozzle_tip_click=(50, 75), show=False,
                                   proposer=lambda frame: next(predictions))
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['frames_processed'], 4)
            self.assertEqual(result['analysis_type'], 'post_analysis_printer_head')
            self.assertTrue((root / 'post_analysis_printer_head_annotated.mp4').is_file())
            self.assertTrue((root / 'POST_ANALYSIS_PRINTER_HEAD.json').is_file())
            self.assertTrue(source.is_file())
            self.assertEqual(trajectory.read_text(), 'existing live trajectory\n')
            with (root / 'post_analysis_printer_head_trajectory.csv').open(newline='') as csv_file:
                rows = list(csv.DictReader(csv_file))
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[-1]['tracking_valid'], '1')
            self.assertEqual(float(rows[-1]['head_width_px']), 40)
            self.assertEqual(float(rows[-1]['camera_distance_ratio']), 1)
            self.assertEqual(rows[-1]['camera_distance_mm'], '')
            self.assertAlmostEqual(float(rows[-1]['nozzle_tip_x_px']), 56, delta=2)

    def test_missing_detection_does_not_emit_stale_position(self):
        tracker = PrinterHeadTracker(proposer=lambda frame: [((20, 20, 40, 20), .9)])
        frame = np.zeros((100, 100, 3), np.uint8)
        self.assertTrue(tracker.calibrate(frame, (40, 30), (40, 60)))
        tracker.proposer = lambda frame: []
        self.assertEqual(tracker.update(frame), (None, None))
        self.assertIsNone(tracker.visible)
        tracker.proposer = lambda frame: [((25, 20, 40, 20), .9)]
        center, tip = tracker.update(frame)
        np.testing.assert_allclose(center, [45, 30])
        np.testing.assert_allclose(tip, [45, 60])


if __name__ == '__main__':
    unittest.main()