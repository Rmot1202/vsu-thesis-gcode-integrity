#!/usr/bin/env python3
"""Capture known-position camera samples from the supervised calibration moves."""
import argparse
import csv
import json
from collections import deque
from pathlib import Path
import sys
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application.side_xy_calibration_gcode import CALIBRATION_POINTS
from backend.post_analysis.printer_head import ClickSelection, PrinterHeadTracker
from backend.post_analysis.training.prepare_dataset import ModelProposer
from cameras.side.printer_xy_calibration import fit_side_xy_calibration


class StableStopCollector:
    """Assign settled camera measurements to the known calibration positions."""

    def __init__(self, points=CALIBRATION_POINTS, settle_seconds=1.5,
                 position_jitter_px=5.0, movement_threshold_px=7.0):
        if settle_seconds <= 0 or position_jitter_px <= 0 or movement_threshold_px <= 0:
            raise ValueError("Timing and pixel thresholds must be positive.")
        self.points = tuple(points)
        self.settle_seconds = float(settle_seconds)
        self.position_jitter_px = float(position_jitter_px)
        self.movement_threshold_px = float(movement_threshold_px)
        self.armed = False
        self.movement_seen = False
        self.waiting_for_next_move = False
        self.movement_anchor = None
        self.buffer = deque()
        self.samples = []

    def arm(self):
        self.armed = True
        self.movement_seen = False
        self.waiting_for_next_move = False
        self.movement_anchor = None
        self.buffer.clear()

    def clear_tracking_gap(self):
        self.movement_anchor = None
        self.buffer.clear()

    def _moved(self, current, anchor):
        center_delta = np.linalg.norm(np.asarray(current[:2]) - np.asarray(anchor[:2]))
        width_delta = abs(current[3] - anchor[3]) / max(anchor[3], 1.0)
        return center_delta >= self.movement_threshold_px or width_delta >= 0.025

    def update(self, timestamp, center_xy, nozzle_x_px, head_width_px):
        if not self.armed or len(self.samples) >= len(self.points):
            return None
        values = np.asarray([timestamp, *center_xy, nozzle_x_px, head_width_px], dtype=float)
        if not np.isfinite(values).all() or head_width_px <= 0:
            self.buffer.clear()
            self.movement_anchor = None
            return None
        current = tuple(values.tolist())
        if self.movement_anchor is None:
            self.movement_anchor = current[1:]
        moved = self._moved(current[1:], self.movement_anchor)
        if not self.movement_seen and moved:
            self.buffer.clear()
            self.movement_seen = True
            self.movement_anchor = current[1:]
        elif self.waiting_for_next_move and moved:
            self.buffer.clear()
            self.waiting_for_next_move = False
            self.movement_anchor = current[1:]

        if not self.movement_seen or self.waiting_for_next_move:
            return None
        self.buffer.append(current)
        while self.buffer and current[0] - self.buffer[0][0] > self.settle_seconds:
            self.buffer.popleft()
        if len(self.buffer) < 3 or current[0] - self.buffer[0][0] < self.settle_seconds:
            return None

        values_window = np.asarray([row[1:] for row in self.buffer], dtype=float)
        center_ranges = np.ptp(values_window[:, :2], axis=0)
        width_range = np.ptp(values_window[:, 3])
        if (center_ranges > self.position_jitter_px).any():
            return None
        if width_range > max(2.0, float(np.median(values_window[:, 3])) * 0.02):
            return None

        split, label, x_mm, y_mm = self.points[len(self.samples)]
        sample = {
            "x_mm": float(x_mm),
            "y_mm": float(y_mm),
            "nozzle_x_px": float(np.median(values_window[:, 2])),
            "head_width_px": float(np.median(values_window[:, 3])),
            "split": split,
            "label": label,
        }
        self.samples.append(sample)
        self.buffer.clear()
        self.waiting_for_next_move = True
        self.movement_anchor = (
            float(np.median(values_window[:, 0])),
            float(np.median(values_window[:, 1])),
            float(np.median(values_window[:, 2])),
            float(np.median(values_window[:, 3])),
        )
        return sample


def _draw_status(frame, tracker, collector, armed):
    display = frame.copy()
    if tracker.visible is not None:
        cv2.drawContours(display, [tracker.visible["contour"]], -1, (0, 255, 255), 2)
    if tracker.offset is None:
        status = "Click head, then SHIFT-click nozzle tip"
    elif not armed:
        status = "Press S as calibration point A begins"
    elif len(collector.samples) >= len(collector.points):
        status = "All calibration points collected"
    else:
        split, label, x_mm, y_mm = collector.points[len(collector.samples)]
        status = f"Point {label} ({split}): X={x_mm} Y={y_mm} mm"
    lines = (status, "S arm calibration | Q quit")
    for row, text in enumerate(lines):
        origin = (12, 28 + row * 28)
        cv2.putText(display, text, origin, cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 0, 0), 3)
        cv2.putText(display, text, origin, cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 1)
    return display


def _frame_timestamp(input_is_file, frame_index, fps):
    return frame_index / fps if input_is_file else time.monotonic()


def collect_calibration(input_source, model_path, output_path, depth_axis="X",
                        confidence=0.25, device=None):
    output_path = Path(output_path).expanduser().resolve()
    samples_path = output_path.with_suffix(".csv")
    if output_path.exists() or samples_path.exists():
        raise FileExistsError(f"Refusing to overwrite calibration output: {output_path} or {samples_path}")
    model_path = Path(model_path).expanduser()
    if not model_path.is_file():
        raise ValueError(f"YOLO model does not exist: {model_path}")

    source = str(input_source)
    input_is_file = not source.isdecimal()
    capture = cv2.VideoCapture(int(source) if source.isdecimal() else source)
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open camera/video: {input_source}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not np.isfinite(fps) or fps <= 0:
        fps = 30.0
    tracker = PrinterHeadTracker(
        proposer=ModelProposer(model_path, confidence, device)
    )
    collector = StableStopCollector()
    selection = ClickSelection()
    window = "Side XY calibration | select head and nozzle tip"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, selection.mouse)
    armed = False
    frame_index = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        while len(collector.samples) < len(CALIBRATION_POINTS):
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError(
                    f"Input ended after {len(collector.samples)} of {len(CALIBRATION_POINTS)} "
                    "calibration points were collected."
                )
            marker = tip = None
            if tracker.offset is None:
                tracker.detect(frame)
                if selection.marker is not None and selection.tip is not None:
                    if tracker.calibrate(frame, selection.marker, selection.tip):
                        selection.marker = selection.tip = None
                    else:
                        print(tracker.error, flush=True)
                        selection.marker = selection.tip = None
            else:
                marker, tip = tracker.update(frame)
                if armed and marker is not None and tip is not None:
                    head_width = tracker.visible["bbox"][2]
                    sample = collector.update(
                        _frame_timestamp(input_is_file, frame_index, fps),
                        marker, float(tip[0]), float(head_width),
                    )
                    if sample is not None:
                        print(
                            f"Captured {sample['label']} ({sample['split']}) at "
                            f"X={sample['x_mm']:.0f}, Y={sample['y_mm']:.0f} mm.",
                            flush=True,
                        )
                elif armed:
                    collector.clear_tracking_gap()

            cv2.imshow(window, _draw_status(frame, tracker, collector, armed))
            key = cv2.waitKey(max(1, round(1000 / fps))) & 255
            if key == ord("q"):
                return None
            if key == ord("s") and tracker.offset is not None and not armed:
                collector.arm()
                armed = True
                print("Armed. The next settled point is assigned to A; begin at the first calibration move.", flush=True)
            frame_index += 1
    finally:
        capture.release()
        cv2.destroyAllWindows()

    with samples_path.open("w", newline="") as sample_file:
        fields = ("x_mm", "y_mm", "nozzle_x_px", "head_width_px", "split", "label")
        writer = csv.DictWriter(sample_file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(collector.samples)
    calibration = fit_side_xy_calibration(samples_path, depth_axis)
    output_path.write_text(json.dumps(calibration, indent=2) + "\n")
    return {
        "calibration": output_path,
        "samples": samples_path,
        "validation_rmse_mm": calibration["validation_rmse_mm"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="0", help="Camera index or calibration video")
    parser.add_argument("--model", type=Path, required=True, help="Reviewed printer_head YOLO weights")
    parser.add_argument("--output", type=Path, required=True, help="New calibration JSON path")
    parser.add_argument("--depth-axis", choices=("X", "Y"), default="X")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--device", help="YOLO inference device, e.g. cpu or mps")
    args = parser.parse_args(argv)
    result = collect_calibration(
        args.input, args.model, args.output, args.depth_axis, args.confidence, args.device
    )
    if result is None:
        print("Calibration cancelled; no calibration file was written.")
        raise SystemExit(1)
    print(f"Calibration saved: {result['calibration']}")
    print(f"Samples saved: {result['samples']}")
    print(f"Held-out validation RMSE (mm): {result['validation_rmse_mm']}")


if __name__ == "__main__":
    main()
