"""Register a camera track to a G-code timeline (time offset + time scale).

Two uses:

* calibration: mapping unknown -> for each candidate (offset, scale) fit the
  linear camera->bed mapping by least squares and keep the best residual;
* comparison: mapping known (from a *different* calibration print) -> search
  (offset, scale) that minimise the time-aligned error in millimetres.
"""
from __future__ import annotations

import numpy as np

from . import mapping


class DenseTimeline:
    """Commanded XY sampled every ``dt`` seconds for fast interpolation."""

    def __init__(self, timeline, dt=0.005):
        self.duration = timeline.duration
        self.t = np.arange(0.0, self.duration + dt, dt)
        xy, ext = timeline.sample(self.t)
        self.x, self.y, self.ext = xy[:, 0], xy[:, 1], ext

    def __call__(self, tau):
        return np.column_stack((np.interp(tau, self.t, self.x), np.interp(tau, self.t, self.y)))

    def extruding(self, tau):
        idx = np.clip(np.round(np.asarray(tau) / (self.t[1] - self.t[0])).astype(int), 0, len(self.t) - 1)
        return self.ext[idx]


def _window(track_t, offset, scale, duration):
    tau = (track_t - offset) / scale
    return (tau >= 0) & (tau <= duration), tau


def _candidate_offsets(track_t, duration, step):
    lo = track_t.min() - 0.2 * duration
    hi = track_t.max() - 0.5 * duration
    return np.arange(lo, hi, step)


def register(track, dense, mapper=None, scales=np.arange(0.85, 1.36, 0.025), coarse_step=0.25,
             min_coverage=0.6, model="pinhole_yaw"):
    """Return best dict(offset, scale, rmse, cx, cy, n).

    The coarse search uses the simple ``pinhole_yaw`` model (few parameters, so
    it cannot "explain" a wrong time window); refine the mapping afterwards.
    """
    t = track["t"]
    fps = 1.0 / np.median(np.diff(t)) if len(t) > 1 else 30.0
    best = None

    def evaluate(off, s):
        sel, tau = _window(t, off, s, dense.duration)
        n = int(sel.sum())
        expected_n = dense.duration * s * fps
        if n < 10 or n < min_coverage * expected_n:
            return None
        target = dense(tau[sel])
        obs = mapping.subset(track, sel)
        if mapper is None:
            cx, cy = mapping.fit(obs, target, model)
            pred = mapping.apply(cx, cy, obs, model)
        else:
            cx = cy = None
            pred = mapper(obs)
        err = np.linalg.norm(pred - target, axis=1)
        # penalise windows where the commanded path barely moves (degenerate fits)
        spread = target.std(axis=0).sum()
        if spread < 5:
            return None
        rmse = float(np.sqrt(np.mean(err ** 2)))
        return dict(offset=float(off), scale=float(s), rmse=rmse, cx=cx, cy=cy, n=n)

    for s in scales:
        for off in _candidate_offsets(t, dense.duration * s, coarse_step):
            r = evaluate(off, s)
            if r and (best is None or r["rmse"] < best["rmse"]):
                best = r
    if best is None:
        raise ValueError("Could not register the track to the G-code (no window with enough tracked frames).")
    # local refinement
    for step_o, step_s in ((0.05, 0.01), (0.01, 0.0025)):
        o0, s0 = best["offset"], best["scale"]
        for s in np.arange(s0 - 3 * step_s, s0 + 3.01 * step_s, step_s):
            for off in np.arange(o0 - 6 * step_o, o0 + 6.01 * step_o, step_o):
                r = evaluate(off, s)
                if r and r["rmse"] < best["rmse"]:
                    best = r
    return best
