"""Ultimaker S3 custom black-block fiducial calibration (no ArUco).

Marker identities are assigned by configured image position, not decoded IDs.
Keep all four complete markers visible. Press s for the existing CSRT nozzle
selection workflow, v to record, r to reset calibration, d for debug, q to quit.
A homography measures the marker plane only; nozzle/marker height differences
and lens distortion cause error. This program does not measure Z.
"""
import csv
import itertools
import json
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# ========================= USER SETTINGS =========================
CAMERA_INDEX = 0
CAMERA_WIDTH = 1280
CAMERA_HEIGHT = 720
TARGET_FPS = 30
BED_WIDTH_MM = 230.0
BED_DEPTH_MM = 190.0
# These must describe CENTERS, not lower-left corners of the printed blocks.
# Approximate measured inputs imply approximate recovered coordinates.
MARKER_LABELS = ('FL', 'FR', 'RR', 'RL')
MARKER_LOCATIONS_MM = np.float32([[6, 6], [206, 6], [206, 166], [6, 166]])
MARKER_SIZE_MM = 18.0
# Which physical marker occupies image TL, TR, BR, BL? Default: front at bottom.
# Change this for a rotated or mirrored camera; patterns are NOT decoded.
IMAGE_CORNER_LABELS = ('RL', 'RR', 'FR', 'FL')
BLACK_THRESHOLD = 85             # grayscale values <= this are foreground
USE_ADAPTIVE_THRESHOLD = False
ADAPTIVE_BLOCK_SIZE = 41         # odd, > 1
ADAPTIVE_C = 12
CELL_JOIN_KERNEL = 5             # odd; increase if cells remain disconnected
MIN_MARKER_AREA = 80             # grouped outer contour area in image pixels
MAX_MARKER_AREA = 25000
MIN_BLACK_FILL = 0.12
MAX_CANDIDATES = 16
MIN_BED_IMAGE_AREA_FRACTION = 0.04
MAX_MARKER_AREA_RATIO = 6.0
MARKER_SIZE_TOLERANCE = 0.65      # perspective-corrected side-length tolerance
MAX_FRAME_SHIFT_PX = 35.0
CALIBRATION_CONFIRM_FRAMES = 3
CALIBRATION_STABILITY_PX = 8.0
DEBUG_VIEW = False
PIXELS_PER_MM = 4
GRID_SPACING_MM = 10.0
TRACKER_TYPE = 'CSRT'
OUTPUT_DIR = str(Path(__file__).resolve().parent / 'captures')
RAW_WINDOW_NAME = 'Nozzle Tracker - Camera'
WARPED_WINDOW_NAME = 'Nozzle Tracker - Printer XY (mm)'
ROI_WINDOW_NAME = 'Select Nozzle - Drag box then press ENTER'
DEBUG_WINDOW_NAME = 'Custom markers - threshold and grouped candidates'
NOZZLE_BOX_X_FRACTION = 0.50
NOZZLE_BOX_Y_FRACTION = 1.00


def create_tracker(name: str):
    name = name.upper()

    if name == "CSRT":
        if hasattr(cv2, "TrackerCSRT_create"):
            return cv2.TrackerCSRT_create()
        return cv2.legacy.TrackerCSRT_create()

    if name == "KCF":
        if hasattr(cv2, "TrackerKCF_create"):
            return cv2.TrackerKCF_create()
        return cv2.legacy.TrackerKCF_create()

    raise ValueError(f"Unsupported tracker: {name}")


def estimate_nozzle_point_from_box(box):
    """
    Returns the estimated nozzle point in raw camera pixels
    using a configurable point inside the ROI box.
    """
    x, y, w, h = box
    nozzle_u = x + (w * NOZZLE_BOX_X_FRACTION)
    nozzle_v = y + (h * NOZZLE_BOX_Y_FRACTION)
    return np.array([[[nozzle_u, nozzle_v]]], dtype=np.float32)



def order_quad(points):
    """Return image TL, TR, BR, BL for a roughly upright quadrilateral."""
    points = np.asarray(points, np.float32).reshape(4, 2)
    center = points.mean(axis=0)
    order = np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))
    points = points[order]
    return np.roll(points, -int(np.argmin(points.sum(axis=1))), axis=0)


def diagonal_center(quad):
    """Intersect diagonals: projected square center, unlike black-pixel centroid."""
    a, b, c, d = np.asarray(quad, float)
    system = np.column_stack((c - a, -(d - b)))
    if abs(np.linalg.det(system)) < 1e-6:
        return None
    return (a + np.linalg.solve(system, b - a)[0] * (c - a)).astype(np.float32)


def detect_markers(frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    if USE_ADAPTIVE_THRESHOLD:
        mask = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY_INV, ADAPTIVE_BLOCK_SIZE, ADAPTIVE_C)
    else:
        _, mask = cv2.threshold(gray, BLACK_THRESHOLD, 255, cv2.THRESH_BINARY_INV)
    # Closing joins nearby block cells without assuming any encoded dictionary.
    joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                             np.ones((CELL_JOIN_KERNEL, CELL_JOIN_KERNEL), np.uint8))
    contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if not MIN_MARKER_AREA <= area <= MAX_MARKER_AREA:
            continue
        hull = cv2.convexHull(contour)
        perimeter = cv2.arcLength(hull, True)
        approx = cv2.approxPolyDP(hull, .04 * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = order_quad(approx)
        sides = np.linalg.norm(np.roll(quad, -1, axis=0) - quad, axis=1)
        if sides.min() < 5 or sides.max() / sides.min() > 5:
            continue
        center = diagonal_center(quad)
        if center is None:
            continue
        x, y, width, height = cv2.boundingRect(quad)
        local = np.zeros((height, width), np.uint8)
        cv2.fillConvexPoly(local, (quad - (x, y)).astype(np.int32), 255)
        fill = cv2.countNonZero(cv2.bitwise_and(mask[y:y+height, x:x+width], local)) / max(1, cv2.countNonZero(local))
        if fill < MIN_BLACK_FILL:
            continue
        candidates.append({'center': center, 'quad': quad, 'area': cv2.contourArea(quad)})
    # Large footprints are more likely to be whole patterns than individual cells.
    candidates.sort(key=lambda item: item['area'], reverse=True)
    return candidates[:MAX_CANDIDATES], joined


def compute_homography(image_points):
    """Image ordering and destination ordering are always FL, FR, RR, RL."""
    points = np.asarray(image_points, np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return None
    if not cv2.isContourConvex(points.reshape(-1, 1, 2)):
        return None
    matrix = cv2.getPerspectiveTransform(points, MARKER_LOCATIONS_MM)
    if not np.isfinite(matrix).all() or abs(np.linalg.det(matrix)) < 1e-10:
        return None
    denominator = np.c_[points, np.ones(4)] @ matrix[2]
    if denominator.min() * denominator.max() <= 0:
        return None
    # Reject a projective horizon through the full bed, including extrapolation.
    inverse = np.linalg.inv(matrix)
    bed = bed_boundary_mm()
    denominator = np.c_[bed, np.ones(4)] @ inverse[2]
    if denominator.min() * denominator.max() <= 0:
        return None
    return matrix


def bed_boundary_mm():
    return np.float32([[0, 0], [BED_WIDTH_MM, 0],
                       [BED_WIDTH_MM, BED_DEPTH_MM], [0, BED_DEPTH_MM]])


def transform_points(points, matrix):
    result = cv2.perspectiveTransform(np.asarray(points, np.float32).reshape(-1, 1, 2), matrix).reshape(-1, 2)
    return result if np.isfinite(result).all() else None


def select_markers(candidates, frame_shape, previous=None):
    best = None
    for group in itertools.combinations(candidates, 4):
        centers = np.float32([c['center'] for c in group])
        ordered = order_quad(centers)
        by_image = [min(group, key=lambda c: np.linalg.norm(c['center'] - point)) for point in ordered]
        by_label = dict(zip(IMAGE_CORNER_LABELS, by_image))
        markers = [by_label[label] for label in MARKER_LABELS]
        points = np.float32([c['center'] for c in markers])
        if cv2.contourArea(points) < np.prod(frame_shape[:2]) * MIN_BED_IMAGE_AREA_FRACTION:
            continue
        areas = [c['area'] for c in markers]
        if max(areas) / min(areas) > MAX_MARKER_AREA_RATIO:
            continue
        shift = 0 if previous is None else np.linalg.norm(points - previous, axis=1).max()
        if shift > MAX_FRAME_SHIFT_PX:
            continue
        matrix = compute_homography(points)
        if matrix is None:
            continue
        errors = []
        for marker in markers:
            corners_mm = transform_points(marker['quad'], matrix)
            if corners_mm is None:
                break
            lengths = np.linalg.norm(np.roll(corners_mm, -1, axis=0) - corners_mm, axis=1)
            errors.extend(abs(lengths / MARKER_SIZE_MM - 1))
        if len(errors) != 16 or max(errors) > MARKER_SIZE_TOLERANCE:
            continue
        score = float(np.mean(errors)) + shift / 100
        if best is None or score < best[0]:
            best = (score, markers, points, matrix)
    return best


class Calibration:
    def __init__(self):
        self.reset()

    def reset(self):
        self.matrix = None
        self.points = None
        self.markers = []
        self.proposal = None
        self.confirmations = 0

    def update(self, candidates, shape):
        found = select_markers(candidates, shape, self.points)
        self.matrix = None  # Do not output stale XY after a bed-height change.
        self.markers = []
        if found is None:
            self.proposal = None
            self.confirmations = 0
            return
        _, markers, points, matrix = found
        stable = self.proposal is not None and np.linalg.norm(points - self.proposal, axis=1).max() <= CALIBRATION_STABILITY_PX
        self.confirmations = self.confirmations + 1 if stable else 1
        self.proposal = points
        self.markers = markers
        if self.confirmations >= CALIBRATION_CONFIRM_FRAMES:
            self.points = points
            self.matrix = matrix


def bed_view_transform():
    # Front-left origin is at the bottom-left of the displayed top-down view.
    return np.array([[PIXELS_PER_MM, 0, 0],
                     [0, -PIXELS_PER_MM, BED_DEPTH_MM * PIXELS_PER_MM],
                     [0, 0, 1]], float)


def draw_grid(image):
    height, width = image.shape[:2]
    step = max(1, round(GRID_SPACING_MM * PIXELS_PER_MM))
    for x in range(0, width, step):
        cv2.line(image, (x, 0), (x, height - 1), (65, 90, 65), 1)
    for y in range(height - 1, -1, -step):
        cv2.line(image, (0, y), (width - 1, y), (65, 90, 65), 1)
    cv2.rectangle(image, (0, 0), (width - 1, height - 1), (0, 255, 255), 2)
    return image


def text(image, message, row, color=(255, 255, 255)):
    cv2.putText(image, message, (15, 28 + row * 27), cv2.FONT_HERSHEY_SIMPLEX,
                .6, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, message, (15, 28 + row * 27), cv2.FONT_HERSHEY_SIMPLEX,
                .6, color, 1, cv2.LINE_AA)


def draw_calibration(frame, calibration):
    for label, marker in zip(MARKER_LABELS, calibration.markers):
        cv2.polylines(frame, [marker['quad'].astype(np.int32)], True, (0, 255, 0), 2)
        center = tuple(np.rint(marker['center']).astype(int))
        cv2.circle(frame, center, 4, (0, 255, 255), -1)
        cv2.putText(frame, label, center, cv2.FONT_HERSHEY_SIMPLEX, .65, (0, 255, 255), 2)
    if calibration.matrix is not None:
        boundary = transform_points(bed_boundary_mm(), np.linalg.inv(calibration.matrix))
        if boundary is not None and abs(boundary).max() < 1e6:
            cv2.polylines(frame, [np.rint(boundary).astype(np.int32)], True, (255, 200, 0), 2)
        text(frame, 'CALIBRATED', 0, (0, 255, 0))
    else:
        text(frame, 'MARKERS NOT FOUND / confirming geometry', 0, (0, 180, 255))


def main():
    if sorted(IMAGE_CORNER_LABELS) != sorted(MARKER_LABELS):
        raise ValueError('IMAGE_CORNER_LABELS must contain FL, FR, RR, RL exactly once')
    cap = cv2.VideoCapture(CAMERA_INDEX)
    writers = []
    csv_file = None
    tracker = None
    tracking = False
    recording = False
    debug = DEBUG_VIEW
    calibration = Calibration()
    width = round(BED_WIDTH_MM * PIXELS_PER_MM) + 1
    height = round(BED_DEPTH_MM * PIXELS_PER_MM) + 1
    display_transform = bed_view_transform()
    try:
        if not cap.isOpened():
            raise RuntimeError(f'Cannot open camera {CAMERA_INDEX}; check camera permissions.')
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        cv2.namedWindow(RAW_WINDOW_NAME)
        cv2.namedWindow(WARPED_WINDOW_NAME)
        print('Automatic black-marker calibration. s=nozzle ROI, v=record, r=reset, d=debug, q=quit')
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            candidates, mask = detect_markers(frame)
            calibration.update(candidates, frame.shape)
            raw_display = frame.copy()
            draw_calibration(raw_display, calibration)
            matrix = calibration.matrix
            grid = np.zeros((height, width, 3), np.uint8) if matrix is None else cv2.warpPerspective(frame, display_transform @ matrix, (width, height))
            grid = draw_grid(grid)
            tracked_ok = False
            image_point = bed_point = None
            if tracking and tracker is not None:
                tracked_ok, box = tracker.update(frame)
                if tracked_ok:
                    # Preserve the original CSRT box and configured tip estimation.
                    x, y, w, h = [int(v) for v in box]
                    cv2.rectangle(raw_display, (x, y), (x+w, y+h), (0, 0, 255), 2)
                    nozzle_raw = estimate_nozzle_point_from_box((x, y, w, h))
                    image_point = nozzle_raw[0, 0]
                    cv2.circle(raw_display, tuple(np.rint(image_point).astype(int)), 5, (255, 0, 255), -1)
                    text(raw_display, f'Nozzle pixel: u={image_point[0]:.1f}, v={image_point[1]:.1f}', 1)
                    if matrix is not None:
                        transformed = transform_points(nozzle_raw, matrix)
                        bed_point = transformed[0] if transformed is not None else None
                else:
                    text(raw_display, 'TRACK LOST - press s to select nozzle again', 1, (0, 0, 255))
            inside = bed_point is not None and 0 <= bed_point[0] <= BED_WIDTH_MM and 0 <= bed_point[1] <= BED_DEPTH_MM
            if bed_point is not None:
                label = f'X={bed_point[0]:.1f} mm, Y={bed_point[1]:.1f} mm'
                text(raw_display, label + ('' if inside else ' (outside bed)'), 2)
                text(grid, label, 0)
                if inside:
                    point = transform_points([bed_point], display_transform)[0]
                    cv2.circle(grid, tuple(np.rint(point).astype(int)), 6, (0, 0, 255), -1)
            else:
                text(raw_display, 'Nozzle XY: unavailable', 2)
            text(raw_display, 's=select nozzle | v=record | r=reset | d=debug | q=quit', 3)
            if recording:
                text(raw_display, 'RECORDING', 4, (0, 0, 255))
                writers[0].write(raw_display)
                # Pad odd dimensions to avoid codec silently cropping bed borders.
                writers[1].write(cv2.copyMakeBorder(grid, 0, height % 2, 0, width % 2, cv2.BORDER_CONSTANT))
                csv_writer.writerow([frame_number, f'{time.monotonic()-recording_start:.6f}',
                                     int(tracked_ok), int(matrix is not None), int(inside),
                                     *(image_point if image_point is not None else ('', '')),
                                     *(bed_point if bed_point is not None else ('', '')),
                                     json.dumps(matrix.tolist()) if matrix is not None else ''])
                frame_number += 1
            cv2.imshow(RAW_WINDOW_NAME, raw_display)
            cv2.imshow(WARPED_WINDOW_NAME, grid)
            if debug:
                diagnostic = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
                for candidate in candidates:
                    cv2.polylines(diagnostic, [candidate['quad'].astype(np.int32)], True, (0, 255, 0), 2)
                cv2.imshow(DEBUG_WINDOW_NAME, diagnostic)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            if key == ord('r'):
                calibration.reset()
            elif key == ord('d'):
                debug = not debug
                if not debug:
                    cv2.destroyWindow(DEBUG_WINDOW_NAME)
            elif key == ord('s'):
                # Same nozzle selection/init logic as the original script.
                roi = cv2.selectROI(ROI_WINDOW_NAME, frame.copy(), fromCenter=False, showCrosshair=True)
                if roi[2] > 0 and roi[3] > 0:
                    tracker = create_tracker(TRACKER_TYPE)
                    tracker.init(frame, tuple(roi))
                    tracking = True
                cv2.destroyWindow(ROI_WINDOW_NAME)
            elif key == ord('v'):
                if recording:
                    for writer in writers:
                        writer.release()
                    writers = []
                    csv_file.close()
                    csv_file = None
                    recording = False
                    print('Recording stopped.')
                else:
                    now = datetime.now()
                    folder = Path(OUTPUT_DIR) / now.strftime('%Y-%m-%d') / now.strftime('%H-%M-%S_%f')
                    folder.mkdir(parents=True, exist_ok=False)
                    for name, size in [('annotated.mp4', (frame.shape[1], frame.shape[0])),
                                       ('grid.mp4', (width + width % 2, height + height % 2))]:
                        writer = cv2.VideoWriter(str(folder/name), cv2.VideoWriter_fourcc(*'mp4v'), TARGET_FPS, size)
                        writers.append(writer)
                        if not writer.isOpened():
                            raise RuntimeError(f'Cannot open video writer: {folder/name}')
                    csv_file = open(folder/'trajectory.csv', 'w', newline='')
                    csv_writer = csv.writer(csv_file)
                    csv_writer.writerow(['frame', 'time_seconds', 'tracking_ok', 'calibration_valid',
                                         'inside_bed', 'nozzle_u_px', 'nozzle_v_px',
                                         'nozzle_x_bed_mm', 'nozzle_y_bed_mm', 'image_to_bed_homography'])
                    metadata = {'bed_size_mm': [BED_WIDTH_MM, BED_DEPTH_MM],
                                'marker_order': MARKER_LABELS, 'marker_locations_mm': MARKER_LOCATIONS_MM.tolist(),
                                'image_corner_labels_TL_TR_BR_BL': IMAGE_CORNER_LABELS,
                                'marker_size_mm': MARKER_SIZE_MM, 'black_threshold': BLACK_THRESHOLD,
                                'reference_points_image_px': calibration.points.tolist() if calibration.points is not None else None,
                                'note': 'XY on marker plane only; each CSV row stores its current homography.'}
                    (folder/'calibration.json').write_text(json.dumps(metadata, indent=2))
                    recording = True
                    recording_start = time.monotonic()
                    frame_number = 0
                    print(f'Recording to {folder}')
    finally:
        for writer in writers:
            writer.release()
        if csv_file is not None:
            csv_file.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
