#!/usr/bin/env python3
"""Track a printer head in a saved video using trained YOLO weights."""
import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

SELECTION_RADIUS_PX = 100
SELECTION_AMBIGUITY_PX = 12
MATCH_RADIUS_PX = 100
MIN_AREA_RATIO = 0.35
MAX_AREA_RATIO = 2.8


class PrinterHeadTracker:
    """Associate YOLO printer-head detections with the selected head."""
    def __init__(self, model=None, confidence=0.25, device=None, proposer=None, initial_distance_mm=None):
        if initial_distance_mm is not None and (not np.isfinite(initial_distance_mm) or initial_distance_mm <= 0):
            raise ValueError("initial_distance_mm must be finite and positive")
        self.initial_distance_mm = initial_distance_mm
        self.anchor_width = None
        if proposer is None:
            if model is None:
                raise ValueError('Provide trained printer_head weights')
            if __package__:
                from .training.prepare_dataset import ModelProposer
            else:
                from training.prepare_dataset import ModelProposer
            proposer = ModelProposer(model, confidence, device)
        self.proposer = proposer
        self.offset = None
        self.anchor_area = None
        self.last_marker = None
        self.visible = None
        self.candidates = []
        self.lost_frames = 0
        self.error = ''

    def detect(self, frame):
        candidates = []
        for (x, y, width, height), confidence in self.proposer(frame):
            if not np.isfinite([x, y, width, height, confidence]).all() or width <= 0 or height <= 0:
                continue
            center = np.array([x + width / 2, y + height / 2], dtype=float)
            contour = np.array([[[x, y]], [[x + width, y]],
                                [[x + width, y + height]], [[x, y + height]]], dtype=np.int32)
            candidates.append({'center': center, 'area': width * height,
                               'bbox': (x, y, width, height), 'confidence': confidence,
                               'contour': contour})
        self.candidates = candidates
        return candidates

    def calibrate(self, frame, marker_click, nozzle_tip_click):
        marker_click = np.asarray(marker_click, dtype=float)
        nozzle_tip_click = np.asarray(nozzle_tip_click, dtype=float)
        self.error = ''
        candidates = self.detect(frame)
        if not candidates:
            self.error = 'No printer head detected; check weights, confidence, and visibility.'
            return False
        ranked = sorted(((float(np.linalg.norm(item['center'] - marker_click)), item)
                         for item in candidates), key=lambda pair: pair[0])
        distance, marker = ranked[0]
        x, y, width, height = marker['bbox']
        inside = x <= marker_click[0] <= x + width and y <= marker_click[1] <= y + height
        if not inside and distance > SELECTION_RADIUS_PX:
            self.error = f'Closest printer head is {distance:.0f}px away; click inside the head box.'
            return False
        if len(ranked) > 1 and ranked[1][0] - distance < SELECTION_AMBIGUITY_PX:
            self.error = 'Printer-head candidates are ambiguous; click nearer the head center.'
            return False
        self.offset = nozzle_tip_click - marker['center']
        self.anchor_width = width
        self.anchor_area = marker['area']
        self.last_marker = marker['center'].copy()
        self.visible = marker
        self.lost_frames = 0
        return True

    def update(self, frame):
        self.visible = None
        candidates = self.detect(frame)
        if self.offset is None or self.last_marker is None:
            return None, None
        matches = [item for item in candidates
                   if MIN_AREA_RATIO <= item['area'] / self.anchor_area <= MAX_AREA_RATIO
                   and np.linalg.norm(item['center'] - self.last_marker) <= MATCH_RADIUS_PX]
        if not matches:
            self.lost_frames += 1
            return None, None
        matches.sort(key=lambda item: np.linalg.norm(item['center'] - self.last_marker))
        if len(matches) > 1:
            first_distance = np.linalg.norm(matches[0]['center'] - self.last_marker)
            next_distance = np.linalg.norm(matches[1]['center'] - self.last_marker)
            if next_distance - first_distance < SELECTION_AMBIGUITY_PX:
                self.lost_frames += 1
                return None, None
        marker = matches[0]
        self.visible = marker
        self.last_marker = marker['center'].copy()
        self.lost_frames = 0
        return marker['center'].copy(), marker['center'] + self.offset * (marker['bbox'][2] / self.anchor_width)


    def depth_measurement(self):
        """Optical-axis estimate from head width; positive displacement is toward camera."""
        if self.visible is None or self.anchor_width is None:
            return ('',) * 6
        width, height = self.visible['bbox'][2:]
        scale = width / self.anchor_width
        ratio = 1.0 / scale
        distance = self.initial_distance_mm * ratio if self.initial_distance_mm is not None else ''
        toward = self.initial_distance_mm - distance if self.initial_distance_mm is not None else ''
        return width, height, scale, ratio, distance, toward


class ClickSelection:
    def __init__(self):
        self.marker = None
        self.tip = None

    def mouse(self, event, x, y, flags, _param):
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        point = np.array([x, y], dtype=float)
        if flags & cv2.EVENT_FLAG_SHIFTKEY:
            self.tip = point
        else:
            self.marker = point


def _annotate(frame, tracker, marker, nozzle_tip, frame_index, status):
    display = frame.copy()
    if tracker.offset is None:
        for index, item in enumerate(tracker.candidates, 1):
            cv2.drawContours(display, [item['contour']], -1, (0, 255, 255), 2)
            cv2.putText(display, f'H{index}', tuple(item['center'].astype(int)),
                        cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 2)
    if tracker.visible is not None:
        cv2.drawContours(display, [tracker.visible['contour']], -1, (0, 255, 255), 2)
        cv2.circle(display, tuple(tracker.visible['center'].astype(int)), 4, (0, 255, 255), -1)
    if marker is not None:
        cv2.drawMarker(display, tuple(np.rint(marker).astype(int)), (0, 255, 255),
                       cv2.MARKER_CROSS, 14, 2)
    if nozzle_tip is not None:
        cv2.drawMarker(display, tuple(np.rint(nozzle_tip).astype(int)), (255, 0, 255),
                       cv2.MARKER_CROSS, 18, 2)
    text = [f'Frame {frame_index} | {status}',
            'Click printer head; SHIFT-click physical nozzle tip | Q quit | SPACE pause',
            f'Printer-head detections: {len(tracker.candidates)}']
    if tracker.error:
        text.append(tracker.error)
    for row, line in enumerate(text):
        origin = (12, 25 + row * 25)
        cv2.putText(display, line, origin, cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 0, 0), 3)
        cv2.putText(display, line, origin, cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 255, 255), 1)
    return display


def process_video(video_path, output_dir=None, marker_click=None, nozzle_tip_click=None,
                  show=True, overwrite=False, model=None, confidence=0.25, device=None, proposer=None,
                  initial_distance_mm=None):
    """Track a printer head from a saved video and write independent outputs.

    Supply first-frame image coordinates for both clicks to run without a GUI;
    otherwise the user selects the head and nozzle tip interactively.
    """
    video_path = Path(video_path).expanduser().resolve()
    if not video_path.is_file():
        raise ValueError(f'Video does not exist: {video_path}')
    output_dir = video_path.parent if output_dir is None else Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / 'post_analysis_printer_head_trajectory.csv'
    video_out_path = output_dir / 'post_analysis_printer_head_annotated.mp4'
    metadata_path = output_dir / 'POST_ANALYSIS_PRINTER_HEAD.json'
    outputs = (csv_path, video_out_path, metadata_path)
    if not overwrite and any(path.exists() for path in outputs):
        raise FileExistsError('Printer-head analysis outputs already exist; choose another output directory or set overwrite=True.')
    if (marker_click is None) != (nozzle_tip_click is None):
        raise ValueError('Provide both marker_click and nozzle_tip_click, or neither.')
    if not show and marker_click is None:
        raise ValueError('Non-interactive processing requires marker and nozzle-tip click coordinates.')

    tracker = PrinterHeadTracker(model, confidence, device, proposer, initial_distance_mm)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f'Cannot open video: {video_path}')
    fps = capture.get(cv2.CAP_PROP_FPS)
    if not np.isfinite(fps) or fps <= 0:
        fps = 30.0
    ok, first_frame = capture.read()
    if not ok:
        capture.release()
        raise ValueError(f'Video has no readable frames: {video_path}')
    height, width = first_frame.shape[:2]
    output_size = (width + width % 2, height + height % 2)
    selection = ClickSelection()
    frame_index = 0
    stopped_early = False

    writer = None
    try:
        if marker_click is None:
            if not show:
                raise ValueError('Interactive selection requires show=True.')
            cv2.namedWindow('Printer head post-analysis', cv2.WINDOW_NORMAL)
            cv2.setMouseCallback('Printer head post-analysis', selection.mouse)
            while selection.marker is None or selection.tip is None:
                tracker.detect(first_frame)
                preview = _annotate(first_frame, tracker, None, None, 0, 'SELECTING')
                cv2.imshow('Printer head post-analysis', preview)
                key = cv2.waitKey(30) & 255
                if key == ord('q') or cv2.getWindowProperty('Printer head post-analysis', cv2.WND_PROP_VISIBLE) < 1:
                    return {'status': 'cancelled', 'frames': 0}
            marker_click, nozzle_tip_click = selection.marker, selection.tip

        if not tracker.calibrate(first_frame, marker_click, nozzle_tip_click):
            raise ValueError(tracker.error)

        writer = cv2.VideoWriter(str(video_out_path), cv2.VideoWriter_fourcc(*'mp4v'), fps, output_size)
        if not writer.isOpened():
            raise RuntimeError(f'Cannot create annotated video: {video_out_path}')
        rows_written = 0
        with csv_path.open('w', newline='') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(['frame_index', 'time_seconds', 'head_x_px', 'head_y_px',
                                 'nozzle_tip_x_px', 'nozzle_tip_y_px', 'tracking_valid', 'tracking_source',
                                 'head_width_px', 'head_height_px', 'head_scale_ratio',
                                 'camera_distance_ratio', 'camera_distance_mm', 'motion_toward_camera_mm'])
            frame = first_frame
            paused = False
            while True:
                if frame_index == 0:
                    marker = tracker.visible['center'].copy()
                    nozzle_tip = marker + tracker.offset
                else:
                    marker, nozzle_tip = tracker.update(frame)
                valid = marker is not None and nozzle_tip is not None
                status = 'PRINTER_HEAD' if valid else 'LOST'
                display = _annotate(frame, tracker, marker, nozzle_tip, frame_index, status)
                padded_frame = cv2.copyMakeBorder(display, 0, output_size[1] - height,
                                                  0, output_size[0] - width, cv2.BORDER_CONSTANT)
                writer.write(padded_frame)
                csv_writer.writerow([frame_index, frame_index / fps,
                                     *(marker if valid else ('', '')),
                                     *(nozzle_tip if valid else ('', '')),
                                     int(valid), 'yolo_printer_head' if valid else 'lost',
                                     *tracker.depth_measurement()])
                rows_written += 1

                if show:
                    cv2.imshow('Printer head post-analysis', display)
                    key = cv2.waitKey(0 if paused else 1) & 255
                    if key == ord('q'):
                        stopped_early = True
                        break
                    if key == ord(' '):
                        paused = not paused

                ok, frame = capture.read()
                if not ok:
                    break
                frame_index += 1
        writer.release()
        result = {
            'analysis_type': 'post_analysis_printer_head',
            'status': 'stopped_early' if stopped_early else 'complete',
            'input_video': str(video_path),
            'frames_processed': rows_written,
            'fps': fps,
            'marker_click_px': np.asarray(marker_click, dtype=float).tolist(),
            'nozzle_tip_click_px': np.asarray(nozzle_tip_click, dtype=float).tolist(),
            'pixel_offset_tip_minus_head': tracker.offset.tolist(),
            'model_weights': str(Path(model).expanduser().resolve()) if model else None,
            'confidence_threshold': confidence,
            'nozzle_estimation': 'head-width-scaled pixel offset from detected head center',
            'depth_estimation': {
                'method': 'pinhole inverse head bounding-box width',
                'reference_width_px': float(tracker.anchor_width),
                'initial_distance_mm': initial_distance_mm,
                'positive_motion': 'toward camera from first frame',
                'assumptions': 'fixed camera and zoom; rigid head with constant orientation; consistent detection boxes',
                'limitations': 'YOLO box jitter, occlusion, and rotation can mimic depth; camera depth is not printer Z or bed XY',
            },
            'trajectory_csv': csv_path.name,
            'annotated_video': video_out_path.name,
        }
        metadata_path.write_text(json.dumps(result, indent=2) + '\n')
        return result
    finally:
        if writer is not None:
            writer.release()
        capture.release()
        if show:
            cv2.destroyAllWindows()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--video', type=Path, required=True, help='Saved input video, e.g. raw_video.mp4')
    parser.add_argument('--output-dir', type=Path, help='Optional output folder; defaults to the video folder')
    parser.add_argument('--no-gui', action='store_true', help='Use coordinates supplied on the command line')
    parser.add_argument('--model', type=Path, required=True, help='Trained printer_head best.pt')
    parser.add_argument('--confidence', type=float, default=0.25)
    parser.add_argument('--device', help='cpu, mps, or 0')
    parser.add_argument('--head-x', type=float)
    parser.add_argument('--head-y', type=float)
    parser.add_argument('--tip-x', type=float)
    parser.add_argument('--tip-y', type=float)
    parser.add_argument('--initial-distance-mm', type=float,
                        help='Optical-axis camera-to-head distance in the first frame; enables mm depth estimates')
    parser.add_argument('--overwrite', action='store_true')
    args = parser.parse_args(argv)
    coordinates = (args.head_x, args.head_y, args.tip_x, args.tip_y)
    if args.no_gui and any(value is None for value in coordinates):
        parser.error('--no-gui requires --head-x, --head-y, --tip-x, and --tip-y')
    if not args.no_gui and any(value is not None for value in coordinates):
        parser.error('Coordinate options require --no-gui')
    kwargs = {}
    if args.no_gui:
        kwargs = {'marker_click': (args.head_x, args.head_y),
                  'nozzle_tip_click': (args.tip_x, args.tip_y)}
    result = process_video(args.video, args.output_dir, show=not args.no_gui,
                           overwrite=args.overwrite, model=args.model,
                           confidence=args.confidence, device=args.device,
                           initial_distance_mm=args.initial_distance_mm, **kwargs)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
