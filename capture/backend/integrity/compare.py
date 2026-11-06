"""Calibrate from one print, compare another print against its G-code.

Outputs (per comparison folder):

* ``side_by_side.png`` - expected G-code path | camera-reconstructed path |
  overlay coloured by deviation | camera frame with the tracked path;
* ``timeseries.png``  - commanded vs observed X(t), Y(t) after registration;
* ``observed_bed_path.csv`` - per-frame bed-mm estimates and expected position;
* ``comparison.json`` - metrics and the integrity verdict.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import mapping
from .align import DenseTimeline, register
from .gcode_timeline import load_timeline
from .track import load_track


# --------------------------------------------------------------------------- calibration
def calibrate(track_csv, gcode, output_json, model="pinhole_plus", holdout_block_s=1.0):
    """Fit the camera->bed mapping from a video of a print whose G-code is known.

    1. register time (offset + scale) with a low-parameter model;
    2. fit the requested mapping on 1-second blocks (even blocks = train);
    3. report held-out RMSE on the odd blocks, then refit on all frames.
    """
    track = load_track(track_csv)
    tl = load_timeline(gcode)
    dense = DenseTimeline(tl)
    best = register(track, dense, mapper=None)
    t = track["t"]
    for _ in range(2):  # alternate: fit mapping on window, re-register time with it
        tau = (t - best["offset"]) / best["scale"]
        sel = (tau >= 0) & (tau <= dense.duration)
        obs, target = mapping.subset(track, sel), dense(tau[sel])
        cx, cy = mapping.fit(obs, target, model)
        cal = mapping.to_json(cx, cy, model, 810.0, {})
        refined = register(track, dense, mapper=mapping.BedMapper(cal),
                           scales=np.arange(best["scale"] - 0.05, best["scale"] + 0.051, 0.005),
                           coarse_step=0.05)
        if abs(refined["offset"] - best["offset"]) < 2.0:
            best.update(offset=refined["offset"], scale=refined["scale"])
    tau = (t - best["offset"]) / best["scale"]
    sel = (tau >= 0) & (tau <= dense.duration)
    obs, target = mapping.subset(track, sel), dense(tau[sel])
    block = (np.floor(tau[sel] / holdout_block_s).astype(int) % 2) == 0
    cx_tr, cy_tr = mapping.fit(mapping.subset(obs, block), target[block], model)
    hold_err = np.linalg.norm(mapping.apply(cx_tr, cy_tr, mapping.subset(obs, ~block), model) - target[~block], axis=1)
    cx, cy = mapping.fit(obs, target, model)
    fit_err = np.linalg.norm(mapping.apply(cx, cy, obs, model) - target, axis=1)
    cal = mapping.to_json(cx, cy, model, 810.0, {
        "calibration_video_track": str(track_csv),
        "calibration_gcode": str(gcode),
        "print_start_video_s": best["offset"],
        "time_scale_video_per_gcode_s": best["scale"],
        "samples": int(sel.sum()),
        "fit_rmse_mm": float(np.sqrt(np.mean(fit_err ** 2))),
        "holdout_rmse_mm": float(np.sqrt(np.mean(hold_err ** 2))),
        "holdout_note": f"alternate {holdout_block_s:g}s blocks: fit on even blocks, tested on odd blocks",
        "training_bounds_mm": {"x": [float(target[:, 0].min()), float(target[:, 0].max())],
                               "y": [float(target[:, 1].min()), float(target[:, 1].max())]},
        "valid_z_mm": "first layer only (bed height at calibration); re-calibrate or model Z for other layers",
    })
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(cal, indent=2) + "\n")
    return cal


# --------------------------------------------------------------------------- metrics
def _point_to_polyline(points, poly):
    """Distance from each point to the nearest densely-sampled polyline vertex."""
    from scipy.spatial import cKDTree

    if len(points) == 0 or len(poly) == 0:
        return np.full(len(points), np.nan)
    return cKDTree(poly).query(points)[0]


def default_tolerance(calibration, k=2.5, floor_mm=5.0):
    """Pass/fail tolerance tied to how well the camera was calibrated.

    k * held-out calibration RMSE (k=2.5 ~ the 99th percentile of a 2-D
    Gaussian error with that RMSE), never below ``floor_mm``.
    """
    cal = json.loads(Path(calibration).read_text()) if not isinstance(calibration, dict) else calibration
    rmse = cal.get("holdout_rmse_mm")
    return float(max(floor_mm, round(k * rmse, 1))) if rmse else floor_mm


def compare(track_csv, gcode, calibration, output_dir=None, video=None, tolerance_mm=None,
            label=None, make_figures=True):
    if tolerance_mm is None:
        tolerance_mm = default_tolerance(calibration)
    track = load_track(track_csv)
    tl = load_timeline(gcode)
    dense = DenseTimeline(tl)
    mapper = mapping.BedMapper(calibration)
    best = register(track, dense, mapper=mapper)
    t, px, py = track["t"], track["px"], track["py"]
    tau = (t - best["offset"]) / best["scale"]
    sel = (tau >= 0) & (tau <= dense.duration)
    obs = mapper(mapping.subset(track, sel))
    exp = dense(tau[sel])
    ext = dense.extruding(tau[sel])
    err_t = np.linalg.norm(obs - exp, axis=1)
    ref_poly = tl.polyline(extruding_only=True, step_mm=0.25)
    obs_print = obs[ext]
    d_obs_to_ref = _point_to_polyline(obs_print, ref_poly)
    d_ref_to_obs = _point_to_polyline(ref_poly, obs_print)
    fps = 1.0 / np.median(np.diff(t))
    coverage = float(sel.sum() / max(1, dense.duration * best["scale"] * fps))
    metrics = {
        "label": label,
        "gcode": str(gcode),
        "track": str(track_csv),
        "calibration": str(calibration),
        "print_start_video_s": best["offset"],
        "print_end_video_s": best["offset"] + dense.duration * best["scale"],
        "time_scale": best["scale"],
        "frames_compared": int(sel.sum()),
        "tracking_coverage": coverage,
        "time_aligned_rmse_mm": float(np.sqrt(np.mean(err_t ** 2))),
        "time_aligned_p95_mm": float(np.percentile(err_t, 95)),
        "time_aligned_max_mm": float(err_t.max()),
        "path_deviation_mean_mm": float(np.nanmean(d_obs_to_ref)) if len(d_obs_to_ref) else None,
        "path_deviation_p95_mm": float(np.nanpercentile(d_obs_to_ref, 95)) if len(d_obs_to_ref) else None,
        "reference_covered_fraction": float(np.mean(d_ref_to_obs <= tolerance_mm)) if len(obs_print) else 0.0,
        "hausdorff_mm": float(max(np.nanmax(d_obs_to_ref), np.nanmax(d_ref_to_obs))) if len(obs_print) else None,
        "tolerance_mm": tolerance_mm,
    }
    ok = (metrics["path_deviation_p95_mm"] is not None and metrics["path_deviation_p95_mm"] <= tolerance_mm
          and metrics["reference_covered_fraction"] >= 0.9)
    metrics["verdict"] = "CONSISTENT with G-code" if ok else "DEVIATES from G-code"
    metrics["verdict_rule"] = (f"p95 path deviation <= {tolerance_mm} mm AND >= 90% of the commanded print path "
                               f"has an observed point within {tolerance_mm} mm")
    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        import csv

        with (out / "observed_bed_path.csv").open("w", newline="") as f:
            wr = csv.writer(f)
            wr.writerow(["time_s", "gcode_time_s", "obs_x_mm", "obs_y_mm", "exp_x_mm", "exp_y_mm", "error_mm", "extruding"])
            for row in zip(t[sel], tau[sel], obs[:, 0], obs[:, 1], exp[:, 0], exp[:, 1], err_t, ext):
                wr.writerow([f"{v:.4f}" if isinstance(v, float) else int(v) for v in row])
        (out / "comparison.json").write_text(json.dumps(metrics, indent=2) + "\n")
        if make_figures:
            frame = _grab_frame(video, metrics["print_end_video_s"] + 0.5) if video else None
            _figures(out, tl, obs, exp, ext, err_t, t[sel], px[sel], py[sel], frame, metrics)
    return metrics


def _grab_frame(video, t):
    import cv2

    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, frame = cap.read()
    cap.release()
    return frame[:, :, ::-1] if ok else None


def _figures(out, tl, obs, exp, ext, err, t, px, py, frame, m):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    ref_moves = tl.moves
    allxy = np.vstack([np.array([[mv.x0, mv.y0], [mv.x1, mv.y1]]) for mv in ref_moves] + [obs])
    lo, hi = allxy.min(0) - 10, allxy.max(0) + 10

    def draw_ref(ax, alpha=1.0):
        for mv in ref_moves:
            if mv.extruding:
                ax.plot([mv.x0, mv.x1], [mv.y0, mv.y1], color="#1f5fa8", lw=2.2, alpha=alpha, solid_capstyle="round")
            else:
                ax.plot([mv.x0, mv.x1], [mv.y0, mv.y1], color="#8a8a8a", lw=1, ls="--", alpha=alpha * 0.8)

    def style(ax, title):
        ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_aspect("equal")
        ax.set_xlabel("printer X (mm)"); ax.set_ylabel("printer Y (mm)"); ax.set_title(title, fontsize=12)
        ax.grid(alpha=0.25)

    ncols = 4 if frame is not None else 3
    fig, axes = plt.subplots(1, ncols, figsize=(5.2 * ncols, 6.0))
    draw_ref(axes[0]); style(axes[0], "Expected (slicer G-code)")
    axes[0].plot([], [], color="#1f5fa8", lw=2.2, label="print move")
    axes[0].plot([], [], color="#8a8a8a", lw=1, ls="--", label="travel")
    axes[0].legend(loc="best", fontsize=8, framealpha=0.9)

    axes[1].plot(obs[ext, 0], obs[ext, 1], ".", color="#c0392b", ms=2.5, label="while extruding")
    axes[1].plot(obs[~ext, 0], obs[~ext, 1], ".", color="#e3a49c", ms=1.5, label="travel")
    style(axes[1], "Camera-reconstructed")
    axes[1].legend(loc="best", fontsize=8, framealpha=0.9)

    draw_ref(axes[2], alpha=0.35)
    seg = np.stack([obs[:-1], obs[1:]], axis=1)
    lc = LineCollection(seg, cmap="turbo", norm=plt.Normalize(0, max(10.0, m["tolerance_mm"] * 2)), lw=2)
    lc.set_array(err[:-1]); axes[2].add_collection(lc)
    cb = fig.colorbar(lc, ax=axes[2], fraction=0.046, pad=0.04); cb.set_label("time-aligned error (mm)")
    style(axes[2], "Overlay (colour = deviation)")
    if frame is not None:
        # crop to the region around the tracked path so the head is visible
        hgt, wid = frame.shape[:2]
        y0 = int(max(0, np.min(py) - 0.22 * hgt)); y1 = int(min(hgt, np.max(py) + 0.22 * hgt))
        x0 = int(max(0, np.min(px) - 0.12 * wid)); x1 = int(min(wid, np.max(px) + 0.12 * wid))
        axes[3].imshow(frame[y0:y1, x0:x1], extent=(x0, x1, y1, y0))
        axes[3].plot(px, py, color="#ff3b30", lw=1.2)
        axes[3].set_title("Camera view + tracked head path", fontsize=12); axes[3].axis("off")
    verdict_color = "#1e7d32" if m["verdict"].startswith("CONSISTENT") else "#b71c1c"
    fig.suptitle(f"{m.get('label') or Path(m['gcode']).parent.parent.name}:  {m['verdict']}   |   "
                 f"p95 path deviation {m['path_deviation_p95_mm']:.1f} mm, time-aligned RMSE "
                 f"{m['time_aligned_rmse_mm']:.1f} mm, coverage {100*m['reference_covered_fraction']:.0f}%",
                 fontsize=13, color=verdict_color)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out / "side_by_side.png", dpi=130, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)

    fig, ax = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
    for i, name in enumerate("XY"):
        ax[i].plot(t, exp[:, i], color="#1f5fa8", lw=2, label=f"G-code {name}")
        ax[i].plot(t, obs[:, i], ".", color="#c0392b", ms=2, label=f"camera {name}")
        ax[i].set_ylabel(f"{name} (mm)"); ax[i].grid(alpha=0.3); ax[i].legend(fontsize=8)
    ax[1].set_xlabel("video time (s)")
    fig.suptitle("Commanded vs observed position after time registration")
    fig.tight_layout(); fig.savefig(out / "timeseries.png", dpi=110); plt.close(fig)
