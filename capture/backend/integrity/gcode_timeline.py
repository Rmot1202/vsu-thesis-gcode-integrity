"""Turn slicer G-code into a time-indexed commanded XY path.

The camera sees *when* the head is where, so comparing against G-code needs a
commanded timeline, not just a list of points.  Each linear move is timed with
a trapezoidal velocity profile using the active acceleration (M204) and the
programmed feedrate (F).  Junction speeds follow the classic Marlin/Griffin
"jerk" rule (M205 X/Y: the per-axis velocity change allowed at a corner), with
forward/backward look-ahead passes so every move is reachable under the
acceleration limit.  This is still an approximation of the firmware planner,
which is why the comparison step also estimates a small global time scale.

Only the *print section* is returned: from the first XY move after the slicer
start block (``;LAYER_COUNT`` / first ``;LAYER:``) to the last extruding move.
Firmware-driven motion (homing, active levelling, G280 priming) is not in the
file, so it is not modelled.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d*\.\d+|\d+\.?\d*))")


@dataclass
class Move:
    t0: float
    t1: float
    x0: float
    y0: float
    x1: float
    y1: float
    z: float | None
    extruding: bool
    feed_mm_s: float
    line: int


@dataclass
class Timeline:
    moves: list[Move]
    source: str
    layers: list[str] = field(default_factory=list)
    slicer_print_time_s: float | None = None

    @property
    def duration(self) -> float:
        return self.moves[-1].t1 if self.moves else 0.0

    def print_moves(self):
        return [m for m in self.moves if m.extruding]

    def sample(self, t):
        """Commanded XY at times ``t`` (seconds from print-section start)."""
        t = np.asarray(t, dtype=float)
        t0 = np.array([m.t0 for m in self.moves])
        t1 = np.array([m.t1 for m in self.moves])
        idx = np.clip(np.searchsorted(t1, t, side="left"), 0, len(self.moves) - 1)
        out = np.empty((t.size, 2))
        ext = np.zeros(t.size, dtype=bool)
        for k, i in enumerate(np.atleast_1d(idx)):
            m = self.moves[i]
            tt = float(np.atleast_1d(t)[k])
            frac = _trapezoid_fraction(m, tt)
            out[k] = (m.x0 + frac * (m.x1 - m.x0), m.y0 + frac * (m.y1 - m.y0))
            ext[k] = m.extruding and t0[i] <= tt <= t1[i]
        return out, ext

    def polyline(self, extruding_only=True, step_mm=0.5):
        """Densely sampled reference path (for time-free distance metrics)."""
        pts = []
        for m in self.moves:
            if extruding_only and not m.extruding:
                continue
            length = math.hypot(m.x1 - m.x0, m.y1 - m.y0)
            n = max(2, int(math.ceil(length / step_mm)) + 1)
            f = np.linspace(0, 1, n)
            pts.append(np.column_stack((m.x0 + f * (m.x1 - m.x0), m.y0 + f * (m.y1 - m.y0))))
        return np.vstack(pts) if pts else np.empty((0, 2))


def _trapezoid_fraction(m: Move, t: float) -> float:
    """Distance fraction travelled at absolute time t on move m."""
    if t <= m.t0:
        return 0.0
    if t >= m.t1:
        return 1.0
    length = math.hypot(m.x1 - m.x0, m.y1 - m.y0)
    if length <= 1e-9:
        return 1.0
    a = getattr(m, "_accel", 1500.0)
    vin, vc, vout = getattr(m, "_vin", 0.0), getattr(m, "_vc", m.feed_mm_s), getattr(m, "_vout", 0.0)
    t1 = (vc - vin) / a
    t3 = (vc - vout) / a
    d1 = (vc * vc - vin * vin) / (2 * a)
    tau = t - m.t0
    if tau < t1:
        s = vin * tau + 0.5 * a * tau * tau
    elif tau < (m.t1 - m.t0) - t3:
        s = d1 + vc * (tau - t1)
    else:
        r = (m.t1 - m.t0) - tau  # time remaining
        s = length - (vout * r + 0.5 * a * r * r)
    return min(1.0, max(0.0, s / length))


def _unit(m):
    length = math.hypot(m.x1 - m.x0, m.y1 - m.y0)
    if length <= 1e-9:
        return None, 0.0
    return ((m.x1 - m.x0) / length, (m.y1 - m.y0) / length), length


def _plan(moves, use_jerk=True):
    """Assign entry/cruise/exit speeds and start/end times to every move."""
    n = len(moves)
    units = [_unit(m) for m in moves]
    vmax = [m.feed_mm_s if units[i][0] else 0.0 for i, m in enumerate(moves)]
    # junction speed limit between move i-1 and i (index i), and at both ends
    junction = [0.0] * (n + 1)
    if use_jerk:
        for i in range(1, n):
            u0, u1 = units[i - 1][0], units[i][0]
            if u0 is None or u1 is None:
                continue
            jerk = getattr(moves[i], "_jerk", 20.0)
            dv = max(abs(u1[0] - u0[0]), abs(u1[1] - u0[1]))
            vj = min(vmax[i - 1], vmax[i])
            if dv > 1e-9:
                vj = min(vj, jerk / dv)
            junction[i] = vj
    # forward / backward passes so the junction speeds are reachable
    for i in range(n):
        length = units[i][1]
        a = getattr(moves[i], "_accel", 1500.0)
        junction[i + 1] = min(junction[i + 1], math.sqrt(junction[i] ** 2 + 2 * a * length))
    for i in range(n - 1, -1, -1):
        length = units[i][1]
        a = getattr(moves[i], "_accel", 1500.0)
        junction[i] = min(junction[i], math.sqrt(junction[i + 1] ** 2 + 2 * a * length))
    t = 0.0
    for i, m in enumerate(moves):
        length = units[i][1]
        a = getattr(m, "_accel", 1500.0)
        if length <= 1e-9:  # dwell or zero-length move
            dur = m.t1 - m.t0
            m._vin = m._vc = m._vout = 0.0
        else:
            vin, vout, vc = junction[i], junction[i + 1], vmax[i]
            d1 = (vc * vc - vin * vin) / (2 * a)
            d3 = (vc * vc - vout * vout) / (2 * a)
            if d1 + d3 > length:  # never reaches cruise speed
                vc = math.sqrt(max(vin * vin, vout * vout, (2 * a * length + vin * vin + vout * vout) / 2))
                d1 = (vc * vc - vin * vin) / (2 * a)
                d3 = (vc * vc - vout * vout) / (2 * a)
            dur = (vc - vin) / a + (vc - vout) / a + max(0.0, length - d1 - d3) / max(vc, 1e-9)
            m._vin, m._vc, m._vout = vin, vc, vout
        m.t0, m.t1 = t, t + dur
        t += dur


def _move_time(length: float, v: float, a: float) -> float:
    if length <= 0:
        return 0.0
    if v * v / a >= length:  # never reaches cruise speed
        return 2.0 * math.sqrt(length / a)
    return length / v + v / a


def load_timeline(path, default_accel=1500.0, travel_accel=None, default_jerk=20.0, use_jerk=True) -> Timeline:
    path = Path(path)
    text = path.read_text(errors="replace").splitlines()
    x = y = z = None
    e = 0.0
    feed = 50.0  # mm/s
    accel = default_accel
    jerk = default_jerk
    abs_xyz = True
    abs_e = True
    started = False
    moves: list[Move] = []
    layers: list[str] = []
    t = 0.0
    print_time = None
    for lineno, raw in enumerate(text, 1):
        m = re.match(r";PRINT\.TIME:(\d+(?:\.\d+)?)", raw.strip())
        if m:
            print_time = float(m.group(1))
        m = re.match(r";\s*LAYER:\s*(-?\d+)", raw.strip())
        if m:
            layers.append(m.group(1))
        if raw.strip().startswith(";LAYER_COUNT") or raw.strip().startswith(";LAYER:"):
            started = True
        code = raw.split(";", 1)[0].strip().upper()
        words = WORD.findall(code)
        if not words:
            continue
        letter, value = words[0]
        cmd = f"{letter}{int(float(value))}"
        vals = {k: float(v) for k, v in words[1:]}
        if cmd == "G90":
            abs_xyz = True
        elif cmd == "G91":
            abs_xyz = False
        elif cmd == "M82":
            abs_e = True
        elif cmd == "M83":
            abs_e = False
        elif cmd == "G92":
            if "E" in vals:
                e = vals["E"]
            x = vals.get("X", x)
            y = vals.get("Y", y)
            z = vals.get("Z", z)
        elif cmd == "M204":
            accel = vals.get("S", vals.get("P", accel)) or accel
        elif cmd == "M205":
            jerk = min(vals.get("X", jerk), vals.get("Y", vals.get("X", jerk)))
        elif cmd in ("G0", "G1"):
            if "F" in vals:
                feed = vals["F"] / 60.0
            nx = vals.get("X", x if abs_xyz else 0.0)
            ny = vals.get("Y", y if abs_xyz else 0.0)
            if not abs_xyz:
                nx = (x or 0.0) + vals.get("X", 0.0)
                ny = (y or 0.0) + vals.get("Y", 0.0)
            nz = vals.get("Z", z) if abs_xyz else (z or 0.0) + vals.get("Z", 0.0)
            ne = e
            if "E" in vals:
                ne = vals["E"] if abs_e else e + vals["E"]
            has_xy = "X" in vals or "Y" in vals
            if started and has_xy and x is not None and y is not None and nx is not None and ny is not None:
                length = math.hypot(nx - x, ny - y)
                a = accel if (ne - e) > 1e-9 or travel_accel is None else travel_accel
                dur = _move_time(length, feed, a)
                mv = Move(t, t + dur, x, y, nx, ny, nz, (ne - e) > 1e-9 and cmd == "G1", feed, lineno)
                mv._accel = a
                mv._jerk = jerk
                moves.append(mv)
                t += dur
            x, y, z, e = nx, ny, nz, ne
        elif cmd == "G4":
            dwell = vals.get("P", 0.0) / 1000.0 + vals.get("S", 0.0)
            if started and dwell > 0 and x is not None:
                mv = Move(t, t + dwell, x, y, x, y, z, False, 0.0, lineno)
                moves.append(mv)
                t += dwell
    # trim trailing non-extruding moves (end-gcode / park is firmware-driven)
    while moves and not moves[-1].extruding:
        moves.pop()
    if not moves:
        raise ValueError(f"No print moves found in {path}")
    _plan(moves, use_jerk)
    return Timeline(moves, str(path), layers, print_time)
