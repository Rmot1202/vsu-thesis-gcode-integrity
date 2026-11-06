#!/usr/bin/env python3
"""Capture calibrated printer-space XY from a side-camera YOLO head tracker."""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

CAPTURE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CAPTURE_ROOT))
from backend.experiment_log import Experiment, RUNS_ROOT, finish_capture, startup_cleanup
from backend.post_analysis.printer_head import ClickSelection, PrinterHeadTracker, _annotate
from backend.post_analysis.training.prepare_dataset import ModelProposer
from backend.tracking_common import MovementTrigger
from cameras.side.printer_xy_calibration import SideXYMapper


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="0", help="Camera index or video file")
    parser.add_argument("--model", type=Path, required=True, help="Trained printer_head YOLO weights")
    parser.add_argument("--xy-calibration", type=Path, required=True, help="Validated side-to-bed XY JSON")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_ROOT)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--no-reference-prompt", action="store_true", default=True)
    parser.add_argument("--stop-file", type=Path, help="Stop after the GUI requests a clean camera shutdown")
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--device", help="YOLO inference device, for example cpu, mps, or 0")
    args = parser.parse_args(argv)
    if not args.model.is_file():
        parser.error(f"YOLO model does not exist: {args.model}")
    if not args.xy_calibration.is_file():
        parser.error(f"Side XY calibration does not exist: {args.xy_calibration}")
    startup_cleanup(args.runs_dir)
    mapper = SideXYMapper(args.xy_calibration)
    calibration_copy = json.loads(args.xy_calibration.read_text())
    calibration_copy["coordinate_model"] = "calibrated_side_xy"
    tracker = PrinterHeadTracker(proposer=ModelProposer(args.model, args.confidence, args.device))
    capture = cv2.VideoCapture(int(args.input) if args.input.isdecimal() else args.input)
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open camera/video: {args.input}")
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not np.isfinite(fps) or fps <= 0:
        fps = 30.0
    selection = ClickSelection()
    window = "Side view | click head; SHIFT-click nozzle | V record | Q quit"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, selection.mouse)
    movement = MovementTrigger()
    session = None
    video_writer = None
    csv_file = None
    csv_writer = None
    frames_recorded = 0
    recording_started = None
    frame_index = 0
    paused = False
    close_status = "interrupted"

    def start_recording(frame, reason):
        nonlocal session, video_writer, csv_file, csv_writer, recording_started, frames_recorded
        session = Experiment.create(args.runs_dir, "side_view", {
            "input": args.input, "camera_view": "side",
            "model": str(args.model.expanduser().resolve()),
            "side_xy_calibration": calibration_copy,
            "recording_trigger": reason,
        })
        session.copy_input(args.xy_calibration, "calibration_input.json")
        height, width = frame.shape[:2]
        size = (width + width % 2, height + height % 2)
        video_writer = cv2.VideoWriter(
            str(session.folder / "annotated_video.mp4"),
            cv2.VideoWriter_fourcc(*"mp4v"), fps, size,
        )
        if not video_writer.isOpened():
            raise RuntimeError("Cannot create side-view annotated video.")
        csv_file = (session.folder / "trajectory.csv").open("w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([
            "frame_index", "elapsed_seconds", "head_x_px", "head_y_px",
            "nozzle_tip_x_px", "nozzle_tip_y_px", "head_width_px", "head_height_px",
            "x_mm", "y_mm", "tracking_valid", "calibration_valid",
            "detection_confidence", "tracking_source", "view_mode", "coordinate_source",
        ])
        recording_started = time.monotonic()
        frames_recorded = 0
        print(f"Recording: {session.folder}", flush=True)

    def close_recording(status):
        nonlocal session, video_writer, csv_file, csv_writer
        nonlocal frames_recorded
        if video_writer is not None:
            video_writer.release()
            video_writer = None
        if csv_file is not None:
            csv_file.close()
            csv_file = None
            csv_writer = None
        finished = session
        session = None
        if finished is not None:
            finish_capture(finished, args, frames_recorded, status)
            print(f"Completed side-camera run: {finished.folder}", flush=True)

    try:
        while True:
            if args.stop_file is not None and args.stop_file.exists():
                close_status = "finished"
                break
            ok, frame = capture.read()
            if not ok:
                close_status = "finished"
                break
            marker = tip = xy = None
            if tracker.offset is None and selection.marker is not None and selection.tip is not None:
                if tracker.calibrate(frame, selection.marker, selection.tip):
                    movement.reset()
                    selection.marker = selection.tip = None
                else:
                    selection.marker = selection.tip = None
            elif tracker.offset is None:
                tracker.detect(frame)
            else:
                marker, tip = tracker.update(frame)
            if tracker.offset is not None and tracker.visible is not None:
                marker = tracker.visible["center"].copy() if marker is None else marker
                if tip is None:
                    tip = marker + tracker.offset
                width, height, *_ = tracker.depth_measurement()
                if width != "":
                    xy = mapper.estimate(float(width), float(tip[0]))

            display = _annotate(
                frame, tracker,
                marker if tracker.offset is not None else None,
                tip if tracker.offset is not None else None,
                frame_index,
                "CALIBRATE" if tracker.offset is None else (
                    "XY " + (f"X={xy[0]:.1f} Y={xy[1]:.1f} mm" if xy is not None else "outside calibration range")
                ),
            )
            cv2.putText(display, "Side camera | YOLO head width -> depth; nozzle x -> lateral XY",
                        (12, display.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX, .48, (255, 255, 255), 1)

            if session is None and tracker.offset is not None and movement.update(
                np.asarray(xy, dtype=float) if xy is not None else None
            ):
                start_recording(frame, "printer_xy_movement")
            cv2.imshow(window, display)
            key = cv2.waitKey(30 if paused else max(1, round(1000 / fps))) & 255

            if session is not None and not paused:
                height, width = frame.shape[:2]
                video_writer.write(cv2.copyMakeBorder(
                    display, 0, height % 2, 0, width % 2, cv2.BORDER_CONSTANT
                ))
                values = tracker.depth_measurement()
                csv_writer.writerow([
                    frames_recorded, time.monotonic() - recording_started,
                    *(marker if marker is not None else ("", "")),
                    *(tip if tip is not None else ("", "")),
                    values[0] if values[0] != "" else "",
                    values[1] if values[1] != "" else "",
                    *(xy if xy is not None else ("", "")),
                    int(marker is not None and tip is not None),
                    int(xy is not None),
                    tracker.visible["confidence"] if tracker.visible is not None else "",
                    "yolo_printer_head" if marker is not None else "lost",
                    "side_view",
                    "calibrated_side_xy",
                ])
                frames_recorded += 1

            if key == ord("q") or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                close_status = "finished"
                break
            if key == ord("v"):
                if session is not None:
                    close_recording("finished")
                    movement.reset()
                elif tracker.offset is not None:
                    start_recording(frame, "manual")
                else:
                    print("Select the detected head and nozzle tip before recording.", flush=True)
            elif key == ord(" "):
                paused = not paused
            frame_index += 1
    finally:
        capture.release()
        cv2.destroyAllWindows()
        close_recording(close_status)


if __name__ == "__main__":
    main()
