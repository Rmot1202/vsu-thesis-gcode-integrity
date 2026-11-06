#!/usr/bin/env python3
"""Colored-pin bed calibration and lightweight automatic nozzle tracking.

Drag on the original video to select/reselect a nozzle ROI in the SAME window.
Keys: space pause, r pause for ROI selection, a automatic reacquisition, q quit.
CSV bed coordinates are normalized-view pixels (top-left origin, Y down).
G-code coordinates are mm (bottom-left origin, Y up), relative to the pin rectangle.
A planar calibration is accurate only for points lying on the reference plane;
nozzle height above it introduces parallax. Export is a measured XY path, not a
reconstruction of extrusion, Z motion, machine origin, or original feed rates.
"""
import argparse
import csv
import itertools
import json
from datetime import datetime
import math
from collections import deque
from pathlib import Path

# Support direct execution from any working directory; shared code is in capture/.
import sys
CAPTURE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CAPTURE_ROOT))
from backend.experiment_log import Experiment, add_capture_arguments, startup_cleanup, finish_capture
from backend.tracking_common import create_tracker as create_shared_tracker

import cv2
import numpy as np


WINDOW = 'Nozzle tracking | drag on video to select nozzle'


def hsv_value(value):
    values = tuple(int(x) for x in value.split(','))
    if len(values) != 3 or not (0 <= values[0] <= 179) or any(not 0 <= x <= 255 for x in values[1:]):
        raise argparse.ArgumentTypeError('Expected H,S,V with H=0..179, S/V=0..255')
    return values


def detect_pins(frame, args):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    low, high = args.hsv_low, args.hsv_high
    if low[0] <= high[0]:
        mask = cv2.inRange(hsv, np.array(low), np.array(high))
    else:  # Hue wraps around red at 179/0.
        mask = cv2.inRange(hsv, np.array((0, low[1], low[2])), np.array(high))
        mask |= cv2.inRange(hsv, np.array(low), np.array((179, high[1], high[2])))
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        perimeter = cv2.arcLength(contour, True)
        _, _, width, height = cv2.boundingRect(contour)
        circularity = 4 * math.pi * area / max(perimeter ** 2, 1)
        if not (args.pin_min_area <= area <= args.pin_max_area and circularity >= args.pin_circularity):
            continue
        if min(width, height) < args.pin_min_size or max(width, height) > args.pin_max_size:
            continue
        moments = cv2.moments(contour)
        if moments['m00']:
            candidates.append((np.array([moments['m10'] / moments['m00'], moments['m01'] / moments['m00']], np.float32), area, circularity))
    return sorted(candidates, key=lambda item: item[2], reverse=True)[:12]


def sort_pins(points):
    """Return TL, TR, BL, BR for a roughly upright bed in the camera view."""
    points = np.asarray(points, np.float32)
    center = points.mean(axis=0)
    cycle = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    cycle = np.roll(cycle, -np.argmin(cycle.sum(axis=1)), axis=0)
    return cycle[[0, 1, 3, 2]]


def compute_homography(points, size, image_shape):
    polygon = points[[0, 1, 3, 2]]
    if not cv2.isContourConvex(polygon.reshape(-1, 1, 2)):
        return None
    if cv2.contourArea(polygon) < image_shape[0] * image_shape[1] * .025:
        return None
    sides = np.roll(polygon, -1, axis=0) - polygon
    lengths = np.linalg.norm(sides, axis=1)
    if lengths.min() < 20 or lengths.max() / lengths.min() > 12:
        return None
    for i in range(4):
        cosine = np.dot(-sides[i - 1], sides[i]) / (lengths[i - 1] * lengths[i])
        if abs(cosine) > .97:
            return None
    width, height = size
    target = np.float32([[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]])
    matrix = cv2.getPerspectiveTransform(points, target)
    # The projective denominator must not cross zero anywhere inside the bed.
    denominators = np.c_[points, np.ones(4)] @ matrix[2]
    if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-10:
        return None
    if denominators.min() * denominators.max() <= 0:
        return None
    return matrix


class Calibration:
    def __init__(self):
        self.points = None
        self.matrix = None
        self.fresh = False

    def update(self, candidates, args, shape):
        self.fresh = False
        best = None
        for group in itertools.combinations(candidates, 4):
            points = sort_pins([item[0] for item in group])
            matrix = compute_homography(points, (args.bed_width, args.bed_height), shape)
            if matrix is None:
                continue
            areas = [item[1] for item in group]
            if max(areas) / min(areas) > 5:
                continue
            distance = 0 if self.points is None else np.linalg.norm(points - self.points, axis=1).max()
            if distance > args.pin_max_shift:
                continue
            score = sum(item[2] for item in group) - np.std(areas) / np.mean(areas) - distance * .1
            if best is None or score > best[0]:
                best = (score, points, matrix)
        if best is not None:
            self.points, self.matrix = best[1:]
            self.fresh = True


def transform_point(point, matrix):
    homogeneous = matrix @ np.array([*point, 1.0])
    if abs(homogeneous[2]) < 1e-8:
        return None
    result = homogeneous[:2] / homogeneous[2]
    return result if np.isfinite(result).all() else None


def smooth_point(point, previous, alpha):
    return point.copy() if previous is None else alpha * point + (1 - alpha) * previous


def clip_box(box, shape):
    x, y, width, height = map(int, box)
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(shape[1], x + width), min(shape[0], y + height)
    return (x1, y1, x2 - x1, y2 - y1) if x2 - x1 >= 8 and y2 - y1 >= 8 else None


def create_tracker():
    return create_shared_tracker('CSRT')


def refine_tip(frame, box, fraction):
    """Search around the tip anchor, independently of the CSRT box edges.

    Fractions may lie outside [0, 1]: the box tracks a stable print-head
    feature while the tip can extend below it. Off-image anchors are invalid.
    """
    x, y, width, height = box
    anchor = np.array([x + fraction[0] * (width - 1),
                       y + fraction[1] * (height - 1)])
    if not (0 <= anchor[0] < frame.shape[1] and 0 <= anchor[1] < frame.shape[0]):
        return None
    radius_x, radius_y = max(width * .15, 2), max(height * .12, 2)
    left = max(0, int(np.floor(anchor[0] - 2 * radius_x)))
    top = max(0, int(np.floor(anchor[1] - 2 * radius_y)))
    right = min(frame.shape[1], int(np.ceil(anchor[0] + 2 * radius_x)) + 1)
    bottom = min(frame.shape[0], int(np.ceil(anchor[1] + 2 * radius_y)) + 1)
    gray = cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    yy, xx = np.mgrid[top:bottom, left:right]
    distance = ((xx - anchor[0]) / radius_x) ** 2 + ((yy - anchor[1]) / radius_y) ** 2
    edges = cv2.Canny(gray, 50, 130).astype(float) / 255
    score = (255 - gray.astype(float)) / 255 + .35 * edges - .7 * distance
    tip_y, tip_x = np.unravel_index(np.argmax(score), score.shape)
    return np.array([left + tip_x, top + tip_y], float)


class NozzleTracker:
    def __init__(self, args):
        self.args = args
        self.tracker = None
        self.box = None
        self.background = cv2.createBackgroundSubtractorMOG2(history=150, varThreshold=30, detectShadows=False)
        self.frames = 0
        self.proposal = None
        self.hits = 0
        self.template = cv2.imread(args.template, cv2.IMREAD_GRAYSCALE) if args.template else None
        if args.template and self.template is None:
            raise ValueError(f'Cannot read template: {args.template}')

    def initialize(self, frame, box):
        box = clip_box(box, frame.shape)
        if box is None:
            return False
        tracker = create_tracker()
        if tracker.init(frame, box) is False:
            return False
        self.tracker, self.box = tracker, box
        self.proposal, self.hits = None, 0
        return True

    def acquire(self, frame, foreground, calibration):
        if self.template is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            height, width = self.template.shape
            if height > gray.shape[0] or width > gray.shape[1]:
                return None
            response = cv2.matchTemplate(gray, self.template, cv2.TM_SQDIFF_NORMED)
            minimum, _, location, _ = cv2.minMaxLoc(response)
            return (*location, width, height) if minimum < self.args.template_error else None
        if self.frames < 25 or calibration.points is None:
            return None
        bed_mask = np.zeros(foreground.shape, np.uint8)
        cv2.fillConvexPoly(bed_mask, calibration.points[[0, 1, 3, 2]].astype(np.int32), 255)
        foreground = cv2.bitwise_and(foreground, bed_mask)
        foreground = cv2.morphologyEx(foreground, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        contours, _ = cv2.findContours(foreground, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            area = cv2.contourArea(contour)
            if self.args.motion_min_area <= area <= self.args.motion_max_area and min(width, height) >= 8:
                boxes.append((area, (x - 8, y - 8, width + 16, height + 16)))
        return max(boxes, key=lambda item: item[0])[1] if boxes else None

    def update(self, frame, calibration):
        foreground = self.background.apply(frame)
        self.frames += 1
        if self.tracker is not None:
            valid, box = self.tracker.update(frame)
            box = clip_box(box, frame.shape) if valid else None
            if box is not None:
                self.box = box
                return refine_tip(frame, box, self.args.tip_anchor)
            self.tracker, self.box = None, None
        if not self.args.manual:
            box = self.acquire(frame, foreground, calibration)
            if box is not None:
                center = np.array([box[0] + box[2] / 2, box[1] + box[3] / 2])
                self.hits = self.hits + 1 if self.proposal is not None and np.linalg.norm(center - self.proposal) < 60 else 1
                self.proposal = center
                if self.hits >= 3 and self.initialize(frame, box):
                    return refine_tip(frame, self.box, self.args.tip_anchor)
            else:
                self.proposal, self.hits = None, 0
        return None


class Interface:
    def __init__(self):
        self.paused = False
        self.start = None
        self.end = None
        self.pending = None
        self.pending_tip = None
        self.scale = 1.0
        self.video_width = 0
        self.video_height = 0

    def mouse(self, event, x, y, flags, userdata):
        inside = 0 <= x < self.video_width and 0 <= y < self.video_height
        if event == cv2.EVENT_LBUTTONDOWN and inside and flags & cv2.EVENT_FLAG_SHIFTKEY:
            self.paused = True
            self.start = self.end = None
            self.pending_tip = (x / self.scale, y / self.scale)
            return
        if event == cv2.EVENT_LBUTTONDOWN and inside:
            self.paused = True
            self.start = self.end = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and self.start is not None:
            self.end = (min(max(x, 0), self.video_width - 1), min(max(y, 0), self.video_height - 1))
        elif event == cv2.EVENT_LBUTTONUP and self.start is not None:
            x0, y0 = self.start
            x1, y1 = self.end
            self.pending = tuple(int(v / self.scale) for v in (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)))
            self.start = self.end = None


def draw_trail(image, trail, index):
    previous = None
    for entry in trail:
        point = None if entry is None else entry[index]
        if point is not None:
            point = tuple(np.rint(point).astype(int))
            if previous is not None:
                cv2.line(image, previous, point, (40, 210, 255), 2, cv2.LINE_AA)
        previous = point


def draw_views(frame, calibration, candidates, tracker, tip, bed_point, trail, args):
    original = frame.copy()
    for center, _, _ in candidates:
        cv2.circle(original, tuple(center.astype(int)), 6, (0, 180, 255), 2)
    if calibration.points is not None:
        for label, point in zip(('TL', 'TR', 'BL', 'BR'), calibration.points):
            p = tuple(point.astype(int))
            cv2.circle(original, p, 8, (80, 255, 80), 2)
            cv2.putText(original, label, p, cv2.FONT_HERSHEY_SIMPLEX, .5, (80, 255, 80), 1)
    draw_trail(original, trail, 0)
    if tracker.box is not None:
        x, y, width, height = tracker.box
        cv2.rectangle(original, (x, y), (x + width, y + height), (255, 180, 30), 2)
    if tip is not None:
        cv2.drawMarker(original, tuple(tip.astype(int)), (50, 50, 255), cv2.MARKER_CROSS, 18, 2)
    bed = np.zeros((args.bed_height, args.bed_width, 3), np.uint8)
    if calibration.matrix is not None:
        bed = cv2.warpPerspective(frame, calibration.matrix, (args.bed_width, args.bed_height))
    draw_trail(bed, trail, 1)
    if bed_point is not None:
        cv2.circle(bed, tuple(bed_point.astype(int)), 5, (50, 50, 255), -1)
    scale = min(1.0, args.display_width / frame.shape[1])
    width, height = round(frame.shape[1] * scale), round(frame.shape[0] * scale)
    original = cv2.resize(original, (width, height))
    bed_width = max(1, round(args.bed_width * height / args.bed_height))
    bed = cv2.resize(bed, (bed_width, height))
    canvas = np.zeros((height + 90, width + bed_width, 3), np.uint8)
    canvas[:height, :width] = original
    canvas[:height, width:] = bed
    status = 'LIVE' if calibration.fresh else ('CACHED' if calibration.matrix is not None else 'SEARCHING')
    coordinate = 'unavailable' if bed_point is None else f'{bed_point[0]:.1f}, {bed_point[1]:.1f} px'
    lines = [f'Pins: {status} | Nozzle: {"TRACKING" if tip is not None else "SEARCHING / drag ROI"} | Bed: {coordinate}',
             'Drag ROI | SHIFT-click tip | SPACE pause | R select | A auto retry | Q quit',
             'Top-down: pin rectangle | Motion auto acquisition is heuristic; verify the selected nozzle.']
    for i, line in enumerate(lines):
        cv2.putText(canvas, line, (12, height + 22 + i * 27), cv2.FONT_HERSHEY_SIMPLEX, .48, (225, 230, 235), 1, cv2.LINE_AA)
    return canvas, scale, width, height


class GCodeWriter:
    def __init__(self, handle, args):
        self.handle, self.args = handle, args
        self.last = None
        self.gap = True
        handle.write('; Camera-derived XY path. Review origin, clearance and calibration before use.\n'
                     '; No Z, extrusion, homing, heating or original timing is reconstructed.\nG21\nG90\n')

    def write(self, point, frame_index):
        if point is None:
            self.gap = True
            return
        x = point[0] / (self.args.bed_width - 1) * self.args.pin_width_mm
        y = (1 - point[1] / (self.args.bed_height - 1)) * self.args.pin_height_mm
        xy = np.array([x, y])
        if self.gap:
            self.handle.write(f'; Segment begins at frame {frame_index}; review travel across any tracking gap.\n')
        if self.gap or self.last is None or np.linalg.norm(xy - self.last) >= self.args.gcode_step_mm:
            self.handle.write(f'G1 X{x:.3f} Y{y:.3f} F{self.args.feedrate:.0f}\n')
            self.last = xy
        self.gap = False


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', default='0', help='Video path or webcam index (default 0)')
    add_capture_arguments(parser)
    parser.add_argument('--output', default='annotated.mp4', help='Video filename within the run folder')
    parser.add_argument('--csv', default='trajectory.csv', help='Optional CSV filename within the run folder')
    parser.add_argument('--gcode', help='Optional G-code filename within the run folder; requires measured pin dimensions')
    parser.add_argument('--hsv-low', type=hsv_value, default=(0, 0, 50))
    parser.add_argument('--hsv-high', type=hsv_value, default=(179, 60, 255))
    parser.add_argument('--bed-width', type=int, default=800)
    parser.add_argument('--bed-height', type=int, default=800)
    parser.add_argument('--pin-width-mm', type=float)
    parser.add_argument('--pin-height-mm', type=float)
    parser.add_argument('--pin-min-area', type=float, default=20)
    parser.add_argument('--pin-max-area', type=float, default=3000)
    parser.add_argument('--pin-min-size', type=int, default=5)
    parser.add_argument('--pin-max-size', type=int, default=100)
    parser.add_argument('--pin-circularity', type=float, default=.45)
    parser.add_argument('--pin-max-shift', type=float, default=25, help='Maximum per-pin calibration change in image pixels')
    parser.add_argument('--manual', action='store_true', help='Disable automatic nozzle acquisition')
    parser.add_argument('--template', help='Optional tightly cropped nozzle image for automatic acquisition')
    parser.add_argument('--template-error', type=float, default=.15)
    parser.add_argument('--motion-min-area', type=float, default=100)
    parser.add_argument('--motion-max-area', type=float, default=15000)
    parser.add_argument('--tip-anchor', type=float, nargs=2, default=(.5, .9), metavar=('X', 'Y'),
                        help='Tip fractions relative to tracked box; values outside 0..1 allowed (e.g. 0.5 1.2)')
    parser.add_argument('--alpha', type=float, default=.35, help='EMA smoothing weight (0 < alpha <= 1)')
    parser.add_argument('--trail-length', type=int, default=3000)
    parser.add_argument('--display-width', type=int, default=960, help='Width of original-video panel')
    parser.add_argument('--feedrate', type=float, default=1200, help='Export feed rate in mm/min; not measured from video')
    parser.add_argument('--gcode-step-mm', type=float, default=.15)
    parser.add_argument('--headless', action='store_true', help='Process without GUI; use auto acquisition or --roi')
    parser.add_argument('--roi', type=int, nargs=4, metavar=('X', 'Y', 'W', 'H'))
    args = parser.parse_args(argv)
    if min(args.bed_width, args.bed_height) < 2 or args.display_width < 100:
        parser.error('Bed dimensions must be >= 2; display width must be >= 100')
    if not 0 < args.alpha <= 1 or any(not math.isfinite(v) for v in args.tip_anchor):
        parser.error('alpha must be in (0,1]; tip anchor values must be finite')
    if args.gcode and (not args.pin_width_mm or not args.pin_height_mm or min(args.pin_width_mm, args.pin_height_mm) <= 0):
        parser.error('--gcode requires positive --pin-width-mm and --pin-height-mm measured center-to-center')
    if args.feedrate <= 0 or args.gcode_step_mm < 0 or args.trail_length < 1:
        parser.error('feedrate/trail-length must be positive; gcode-step-mm must be nonnegative')
    if args.hsv_low[1] > args.hsv_high[1] or args.hsv_low[2] > args.hsv_high[2]:
        parser.error('HSV saturation/value lower limits must not exceed upper limits')
    names = [Path(p).name for p in (args.output, args.csv, args.gcode) if p]
    names = [name if name.startswith('auto_bed_') else 'auto_bed_' + name for name in names]
    if len(set(names)) != len(names) or any(name in ('auto_bed_run.json', '.', '..', '') for name in names):
        parser.error('Output filenames must be distinct and cannot be auto_bed_run.json')
    return args



def create_run_folder(args):
    session=Experiment.create(args.runs_dir,'up_auto_view',vars(args).copy())
    args.output=str(session.folder/'annotated_video.mp4')
    args.csv=str(session.folder/'trajectory.csv')
    if args.gcode:
        args.gcode=str(session.folder/'configured_xy_legacy.gcode')
    return session,session.metadata


def main(argv=None):
    args = parse_args(argv)
    startup_cleanup(args.runs_dir)
    source = int(args.input) if args.input.isdecimal() else args.input
    capture = cv2.VideoCapture(source)
    writer = raw_writer = csv_handle = gcode_handle = None
    session=None
    count = 0
    run_folder = metadata = None
    calibration = None
    completed = False
    try:
        if not capture.isOpened():
            raise RuntimeError(f'Cannot open input: {args.input}')
        session, metadata = create_run_folder(args)
        run_folder=session.folder
        fps = capture.get(cv2.CAP_PROP_FPS)
        fps = fps if math.isfinite(fps) and 0 < fps <= 240 else 30
        for path in (args.output, args.csv, args.gcode):
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        csv_writer = None
        if args.csv:
            csv_handle = open(args.csv, 'w', newline='')
            csv_writer = csv.writer(csv_handle)
            csv_writer.writerow(['frame_index', 'image_x', 'image_y', 'bed_x', 'bed_y', 'homography_valid', 'tracker_valid', 'view_mode'])
        gcode_writer = None
        if args.gcode:
            gcode_handle = open(args.gcode, 'w')
            gcode_writer = GCodeWriter(gcode_handle, args)
        calibration, tracker, ui = Calibration(), NozzleTracker(args), Interface()
        trail, smoothed = deque(maxlen=args.trail_length), None
        if not args.headless:
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(WINDOW, ui.mouse)
        while True:
            valid, frame = capture.read()
            if not valid:
                break
            candidates = detect_pins(frame, args)
            calibration.update(candidates, args, frame.shape)
            if count == 0 and args.roi and not tracker.initialize(frame, args.roi):
                raise ValueError('Initial ROI is too small or outside the image')
            tip = tracker.update(frame, calibration)
            bed_point = transform_point(tip, calibration.matrix) if tip is not None and calibration.matrix is not None else None
            if bed_point is not None and (not 0 <= bed_point[0] < args.bed_width or not 0 <= bed_point[1] < args.bed_height):
                bed_point = None
            smoothed = smooth_point(bed_point, smoothed, args.alpha) if bed_point is not None else None
            trail.append((tip.copy(), smoothed.copy() if smoothed is not None else None) if tip is not None else None)
            canvas, ui.scale, ui.video_width, ui.video_height = draw_views(frame, calibration, candidates, tracker, tip, smoothed, trail, args)
            quit_requested = False
            if not args.headless:
                while True:
                    display = canvas.copy()
                    if ui.start is not None:
                        cv2.rectangle(display, ui.start, ui.end, (255, 200, 50), 2)
                    if ui.paused:
                        cv2.putText(display, 'PAUSED - drag ROI / SHIFT-click tip / SPACE resume', (15, 30), cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 255, 255), 2)
                    cv2.imshow(WINDOW, display)
                    key = cv2.waitKey(30 if ui.paused else max(1, round(1000 / fps))) & 0xFF
                    if key == ord('q') or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                        quit_requested = True
                        break
                    if key == ord(' '):
                        ui.paused = not ui.paused
                    elif key == ord('r'):
                        ui.paused = True
                    elif key == ord('a'):
                        tracker.tracker, tracker.box = None, None
                        tracker.proposal, tracker.hits = None, 0
                        args.manual = False
                        smoothed = None
                        trail.append(None)
                    if ui.pending_tip is not None:
                        if tracker.box is not None:
                            x, y, width, height = tracker.box
                            px, py = ui.pending_tip
                            args.tip_anchor = ((px - x) / (width - 1), (py - y) / (height - 1))
                            tip = refine_tip(frame, tracker.box, args.tip_anchor)
                            bed_point = transform_point(tip, calibration.matrix) if tip is not None and calibration.matrix is not None else None
                            if bed_point is not None and not (0 <= bed_point[0] < args.bed_width and 0 <= bed_point[1] < args.bed_height):
                                bed_point = None
                            smoothed = bed_point
                            trail.append(None)
                            trail.append((tip, smoothed))
                            if gcode_writer:
                                gcode_writer.gap = True
                            canvas, ui.scale, ui.video_width, ui.video_height = draw_views(frame, calibration, candidates, tracker, tip, smoothed, trail, args)
                            print(f'Tip anchor: {args.tip_anchor[0]:.3f} {args.tip_anchor[1]:.3f}', flush=True)
                        else:
                            print('Select a print-head ROI before setting its tip.', flush=True)
                        ui.pending_tip = None
                    if ui.pending is not None:
                        if tracker.initialize(frame, ui.pending):
                            tip = refine_tip(frame, tracker.box, args.tip_anchor)
                            bed_point = transform_point(tip, calibration.matrix) if tip is not None and calibration.matrix is not None else None
                            if bed_point is not None and not (0 <= bed_point[0] < args.bed_width and 0 <= bed_point[1] < args.bed_height):
                                bed_point = None
                            smoothed = bed_point
                            trail.append(None)
                            trail.append((tip, smoothed))
                            if gcode_writer:
                                gcode_writer.gap = True
                            canvas, ui.scale, ui.video_width, ui.video_height = draw_views(frame, calibration, candidates, tracker, tip, smoothed, trail, args)
                            ui.paused = False
                        ui.pending = None
                    if not ui.paused:
                        break
            if writer is None:
                # Many video encoders require even dimensions.
                out_height, out_width = canvas.shape[:2]
                video_size = (out_width + out_width % 2, out_height + out_height % 2)
                writer = cv2.VideoWriter(args.output, cv2.VideoWriter_fourcc(*'mp4v'), fps, video_size)
                if not writer.isOpened():
                    raise RuntimeError(f'Cannot create output video: {args.output}')
            if raw_writer is None:
                raw_size=(frame.shape[1]+frame.shape[1]%2,frame.shape[0]+frame.shape[0]%2)
                raw_writer=cv2.VideoWriter(str(run_folder/'raw_video.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),fps,raw_size)
                if not raw_writer.isOpened():raise RuntimeError('Cannot create raw video')
            raw_writer.write(cv2.copyMakeBorder(frame,0,raw_size[1]-frame.shape[0],0,raw_size[0]-frame.shape[1],cv2.BORDER_CONSTANT))
            writer.write(cv2.copyMakeBorder(canvas, 0, video_size[1] - canvas.shape[0], 0, video_size[0] - canvas.shape[1], cv2.BORDER_CONSTANT))
            if csv_writer:
                csv_writer.writerow([count, *(tip if tip is not None else ('', '')), *(smoothed if smoothed is not None else ('', '')), int(calibration.matrix is not None), int(tip is not None), 'auto_bed_view'])
            if gcode_writer:
                gcode_writer.write(smoothed, count)
            count += 1
            if quit_requested:
                break
        if count == 0:
            raise RuntimeError('Input contains no readable frames')
        completed = True
        print(f'Processed {count} frames. Annotated video: {args.output}')
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if raw_writer is not None:
            raw_writer.release()
        if csv_handle:
            csv_handle.close()
        if gcode_handle:
            gcode_handle.close()
        if not args.headless:
            cv2.destroyAllWindows()
        if run_folder is not None:
            metadata.update(ended_at=datetime.now().astimezone().isoformat(),
                            frames_processed=count, status='finished' if completed else 'interrupted')
            if calibration is not None:
                metadata['final_tip_anchor'] = list(args.tip_anchor)
                metadata['last_pin_centers'] = calibration.points.tolist() if calibration.points is not None else None
                metadata['last_homography'] = calibration.matrix.tolist() if calibration.matrix is not None else None
            (run_folder / 'calibration_snapshot.json').write_text(json.dumps(metadata, indent=2,default=str) + '\n')
            finish_capture(session,args,count,'finished' if completed else 'interrupted')


if __name__ == '__main__':
    main()
