"""Headless printer-head tracking for saved videos.

Unlike ``post_analysis/printer_head.py`` this needs no mouse clicks: the head
is initialised from the most confident ``printer_head`` detection and then
associated frame-to-frame by distance.  The output CSV is the input to
calibration and comparison.

Columns: frame_index,time_s,head_cx_px,head_cy_px,head_w_px,head_h_px,
confidence,valid
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np

FIELDS = ["frame_index", "time_s", "head_cx_px", "head_cy_px", "head_w_px", "head_h_px", "confidence", "valid"]


def _load_net(model):
    from ultralytics import YOLO

    return YOLO(str(model))


def track_video(video, model, output_csv, stride=1, confidence=0.25, device="cpu", imgsz=640,
                max_jump_px=150, progress=True, windows=None, net=None):
    """Track the head.  ``windows`` = list of (start_s, end_s) to restrict dense tracking."""
    net = net or _load_net(model)
    names = net.names if isinstance(net.names, dict) else dict(enumerate(net.names))
    classes = [i for i, n in names.items() if n == "printer_head"]
    if not classes:
        raise ValueError("Weights have no 'printer_head' class; use the trained best.pt, not base COCO weights.")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ValueError(f"Cannot open video {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    last = None
    rows = []
    index = -1
    windows = sorted(windows) if windows else None
    win_i = 0
    while True:
        if windows is not None:
            if win_i >= len(windows):
                break
            ws, we = windows[win_i]
            if index < 0 or index / fps < ws - 1.0:
                target = max(0, int(ws * fps))
                if target > index + 1:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, target)
                    index = target - 1
                    last = None
        ok = cap.grab()
        if not ok:
            break
        index += 1
        if windows is not None and index / fps > windows[win_i][1]:
            win_i += 1
            continue
        if index % stride:
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        t = index / fps
        res = net.predict(frame, conf=confidence, classes=classes, device=device, imgsz=imgsz, verbose=False)[0]
        boxes = res.boxes.xyxy.cpu().numpy() if res.boxes is not None else np.empty((0, 4))
        scores = res.boxes.conf.cpu().numpy() if res.boxes is not None else np.empty(0)
        chosen = None
        if len(boxes):
            centers = np.column_stack(((boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2))
            if last is None:
                k = int(np.argmax(scores))
            else:
                d = np.linalg.norm(centers - last, axis=1)
                k = int(np.argmin(d - 50 * scores))
                if d[k] > max_jump_px:
                    k = None
            if k is not None:
                chosen = (boxes[k], float(scores[k]), centers[k])
        if chosen is None:
            rows.append([index, round(t, 4), "", "", "", "", "", 0])
        else:
            (x1, y1, x2, y2), score, c = chosen
            last = c
            rows.append([index, round(t, 4), round(c[0], 2), round(c[1], 2), round(x2 - x1, 2), round(y2 - y1, 2),
                         round(score, 3), 1])
        if progress and index % (300 * stride) == 0:
            print(f"  frame {index}/{total}", flush=True)
    cap.release()
    output_csv = Path(output_csv)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(rows)
    return output_csv


def motion_windows(coarse_csv, min_move_px=6.0, pad_s=3.0, merge_s=4.0):
    """Time windows where the head moves (or is lost) in a coarse track."""
    import pandas as pd

    d = pd.read_csv(coarse_csv)
    t = d["time_s"].to_numpy(float)
    x = d["head_cx_px"].to_numpy(float)
    w = d["head_w_px"].to_numpy(float)
    moving = np.zeros(len(d), dtype=bool)
    for i in range(1, len(d)):
        if not (np.isfinite(x[i]) and np.isfinite(x[i - 1])):
            continue  # lost detections alone do not open a dense window
        if abs(x[i] - x[i - 1]) > min_move_px or abs(w[i] - w[i - 1]) > min_move_px:
            moving[i] = moving[i - 1] = True
    spans = []
    for i in np.flatnonzero(moving):
        a, b = t[max(0, i - 1)] - pad_s, t[i] + pad_s
        if spans and a <= spans[-1][1] + merge_s:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([max(0.0, a), b])
    return [tuple(s) for s in spans]


def track_two_stage(video, model, output_csv, coarse_stride=15, device="cpu", confidence=0.25):
    """Fast default: coarse pass over the whole video, dense pass where the head moves."""
    output_csv = Path(output_csv)
    net = _load_net(model)
    coarse = output_csv.with_name(output_csv.stem + "_coarse.csv")
    if not coarse.exists():
        track_video(video, model, coarse, stride=coarse_stride, device=device, confidence=confidence, net=net)
    windows = motion_windows(coarse)
    print(f"  dense windows: {[(round(a,1), round(b,1)) for a, b in windows]}")
    return track_video(video, model, output_csv, stride=1, device=device, confidence=confidence,
                       windows=windows, net=net)


def load_track(path, smooth=True):
    """Valid rows as arrays: t, px, py, w, h.

    ``smooth`` applies a 5-frame median then a 9-frame Savitzky-Golay filter
    inside each contiguous run of detections.  The head moves smoothly, while
    YOLO box edges jitter by a few pixels; that jitter is the main noise source
    for the width-based depth estimate.
    """
    import pandas as pd

    d = pd.read_csv(path)
    d = d[d["valid"] == 1].dropna(subset=["head_cx_px", "head_w_px"])
    out = {"t": d["time_s"].to_numpy(float), "frame": d["frame_index"].to_numpy(int),
           "px": d["head_cx_px"].to_numpy(float), "py": d["head_cy_px"].to_numpy(float),
           "w": d["head_w_px"].to_numpy(float), "h": d["head_h_px"].to_numpy(float)}
    if smooth and len(out["t"]) > 9:
        from scipy.signal import medfilt, savgol_filter

        breaks = np.flatnonzero(np.diff(out["frame"]) > 2) + 1
        for key in ("px", "py", "w", "h"):
            parts = []
            for seg in np.split(out[key], breaks):
                if len(seg) >= 9:
                    seg = savgol_filter(medfilt(seg, 5), 9, 2)
                parts.append(seg)
            out[key] = np.concatenate(parts)
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--video", required=True, type=Path)
    p.add_argument("--model", required=True, type=Path, help="Trained printer_head best.pt")
    p.add_argument("--output", required=True, type=Path, help="Trajectory CSV to write")
    p.add_argument("--stride", type=int, default=1, help="Process every Nth frame (with --full)")
    p.add_argument("--full", action="store_true", help="Dense-track every frame (slow on CPU)")
    p.add_argument("--confidence", type=float, default=0.25)
    p.add_argument("--device", default="cpu", help="cpu, mps (Apple GPU) or 0 (CUDA)")
    a = p.parse_args(argv)
    if a.full:
        track_video(a.video, a.model, a.output, a.stride, a.confidence, a.device)
    else:
        track_two_stage(a.video, a.model, a.output, device=a.device, confidence=a.confidence)
    print(f"Wrote {a.output}")


if __name__ == "__main__":
    main()
