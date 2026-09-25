"""Shared CSRT creation, head-light assistance and movement trigger settings.
Used by both camera positions. Camera calibration stays in the view scripts.
"""
import cv2
import numpy as np

MOVEMENT_THRESHOLD_PX = 5.0  # displacement from the last stationary baseline

MOVEMENT_CONFIRM_FRAMES = 3  # reject brief tracker jitter

LED_TRACKING_ENABLED = True

LED_MIN_AREA = 2

LED_MAX_AREA = 500

LED_MIN_SATURATION = 100

LED_MIN_VALUE = 150

LED_SEARCH_RADIUS_PX = 45

LED_MAX_WITHOUT_CSRT_FRAMES = 90

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

class HeadLightTracker:
    """Learn independent red/blue light-to-tip offsets from the selected head ROI."""
    def __init__(self):
        self.offsets = {}
        self.last_tip = None
        self.without_csrt = 0
        self.light = None
        self.color = None

    def update(self, frame, box, csrt_tip):
        self.light = self.color = None
        if not LED_TRACKING_ENABLED:
            return csrt_tip
        self.without_csrt = 0 if csrt_tip is not None else self.without_csrt + 1
        if self.without_csrt > LED_MAX_WITHOUT_CSRT_FRAMES:
            return csrt_tip
        expected_tip = csrt_tip if csrt_tip is not None else self.last_tip
        if expected_tip is None:
            return None
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        choices = []
        for color, hue in [('red', ((hsv[:, :, 0] <= 10) | (hsv[:, :, 0] >= 170))),
                           ('blue', ((hsv[:, :, 0] >= 95) & (hsv[:, :, 0] <= 135)))]:
            mask = (hue & (hsv[:, :, 1] >= LED_MIN_SATURATION) &
                    (hsv[:, :, 2] >= LED_MIN_VALUE)).astype(np.uint8) * 255
            region = np.zeros(mask.shape, np.uint8)
            if color in self.offsets:
                expected_light = expected_tip - self.offsets[color]
                cv2.circle(region, tuple(np.rint(expected_light).astype(int)), LED_SEARCH_RADIUS_PX, 255, -1)
            elif box is not None and csrt_tip is not None:
                x, y, w, h = map(int, box)
                cv2.rectangle(region, (x, y), (x+w, y+h), 255, -1)
                expected_light = np.array([x+w/2, y+h/2])
            else:
                continue
            contours, _ = cv2.findContours(mask & region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            blobs = []
            for contour in contours:
                area = cv2.contourArea(contour)
                if LED_MIN_AREA <= area <= LED_MAX_AREA:
                    moments = cv2.moments(contour)
                    point = np.array([moments['m10']/moments['m00'], moments['m01']/moments['m00']])
                    blobs.append(point)
            # Refuse ambiguous same-color lights rather than silently switch identities.
            if len(blobs) != 1:
                continue
            point = blobs[0]
            if color not in self.offsets:
                self.offsets[color] = csrt_tip - point
            tip = point + self.offsets[color]
            choices.append((float(np.linalg.norm(tip-expected_tip)), color, point, tip))
        if choices:
            _, self.color, self.light, tip = min(choices, key=lambda item: item[0])
            self.last_tip = tip.copy()
            return tip
        self.last_tip = csrt_tip.copy() if csrt_tip is not None else None
        return csrt_tip

class MovementTrigger:
    """Confirm displacement from a baseline; reset across tracking loss/ROI changes."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.origin = None
        self.hits = 0

    def update(self, point):
        if point is None or not np.isfinite(point).all():
            self.reset()
            return False
        point = np.asarray(point, float)
        if self.origin is None:
            self.origin = point.copy()
            return False
        if np.linalg.norm(point - self.origin) >= MOVEMENT_THRESHOLD_PX:
            self.hits += 1
        else:
            self.hits = 0
        return self.hits >= MOVEMENT_CONFIRM_FRAMES
