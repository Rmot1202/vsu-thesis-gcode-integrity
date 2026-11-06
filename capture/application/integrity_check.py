#!/usr/bin/env python3
"""Camera vs G-code integrity check (front/side camera, single layer).

Typical use from the ``capture/`` folder::

    # 1) track the printer head in each video (headless, no clicks)
    python application/integrity_check.py track --video inputs/videos/rect.mp4 \
        --output outputs/integrity/tracks/rect.csv

    # 2) calibrate the camera once from a print whose G-code you trust
    python application/integrity_check.py calibrate \
        --track outputs/integrity/tracks/rect.csv \
        --gcode inputs/gcode/rectangle.gcode \
        --output outputs/integrity/calibration.json

    # 3) check any other print against the G-code that was supposed to run
    python application/integrity_check.py compare \
        --track outputs/integrity/tracks/zig.csv --gcode inputs/gcode/zig.gcode \
        --calibration outputs/integrity/calibration.json \
        --video inputs/videos/zig.mp4 --output-dir outputs/integrity/zig

    # or do track + compare in one go
    python application/integrity_check.py run --video V --gcode G \
        --calibration outputs/integrity/calibration.json --output-dir D

    # every video against every G-code (shows the check can tell prints apart)
    python application/integrity_check.py matrix \
        --track rect=outputs/integrity/tracks/rect.csv --track zig=outputs/integrity/tracks/zig.csv \
        --gcode rect=inputs/gcode/rectangle.gcode --gcode zig=inputs/gcode/zig.gcode \
        --calibration outputs/integrity/calibration.json --output-dir D
"""
import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.integrity.compare import calibrate, compare  # noqa: E402
from backend.integrity.track import track_two_stage  # noqa: E402

DEFAULT_MODEL = ROOT / "assets" / "models" / "printer_head" / "best.pt"


def _print(metrics):
    keys = ["verdict", "print_start_video_s", "time_scale", "time_aligned_rmse_mm",
            "path_deviation_p95_mm", "reference_covered_fraction", "hausdorff_mm"]
    for k in keys:
        v = metrics.get(k)
        print(f"  {k:28s} {v:.3f}" if isinstance(v, float) else f"  {k:28s} {v}")


def cmd_track(a):
    track_two_stage(a.video, a.model, a.output, device=a.device)
    print(f"Track written: {a.output}")


def cmd_calibrate(a):
    cal = calibrate(a.track, a.gcode, a.output, model=a.mapping_model)
    print(f"Calibration written: {a.output}")
    print(f"  print found at video t = {cal['print_start_video_s']:.2f} s, time scale {cal['time_scale_video_per_gcode_s']:.3f}")
    print(f"  fit RMSE {cal['fit_rmse_mm']:.2f} mm, held-out RMSE {cal['holdout_rmse_mm']:.2f} mm "
          f"({cal['holdout_note']})")


def cmd_compare(a):
    m = compare(a.track, a.gcode, a.calibration, a.output_dir, video=a.video, tolerance_mm=a.tolerance, label=a.label)
    print(f"Results in {a.output_dir}")
    _print(m)


def cmd_run(a):
    out = Path(a.output_dir)
    track = out / "track.csv"
    if not track.exists():
        track_two_stage(a.video, a.model, track, device=a.device)
    m = compare(track, a.gcode, a.calibration, out, video=a.video, tolerance_mm=a.tolerance, label=a.label)
    print(f"Results in {out}")
    _print(m)


def _named(items):
    out = []
    for item in items:
        name, _, path = item.partition("=")
        out.append((name, Path(path)))
    return out


def cmd_matrix(a):
    """Every tracked video against every G-code -> matrix.png / matrix.json."""
    import numpy as np
    from backend.integrity.compare import default_tolerance

    tracks, gcodes = _named(a.track), _named(a.gcode)
    tol = a.tolerance or default_tolerance(a.calibration)
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    p95 = np.full((len(tracks), len(gcodes)), np.nan)
    cover = np.full_like(p95, np.nan)
    for i, (tname, t) in enumerate(tracks):
        for j, (gname, g) in enumerate(gcodes):
            try:
                m = compare(t, g, a.calibration, None, tolerance_mm=tol, make_figures=False)
                p95[i, j], cover[i, j] = m["path_deviation_p95_mm"], m["reference_covered_fraction"]
            except ValueError as error:
                print(f"  {tname} vs {gname}: {error}")
            print(f"  video {tname:12s} vs G-code {gname:10s}: p95 {p95[i, j]:6.1f} mm, coverage {cover[i, j]:.2f}")
    json.dump({"videos": [n for n, _ in tracks], "gcodes": [n for n, _ in gcodes], "tolerance_mm": tol,
               "p95_path_deviation_mm": p95.tolist(), "reference_covered_fraction": cover.tolist()},
              open(out / "matrix.json", "w"), indent=2)
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(1.5 * len(gcodes) + 3.5, 0.9 * len(tracks) + 2.2))
    im = ax.imshow(np.minimum(p95, 40), cmap="RdYlGn_r", vmin=0, vmax=40, aspect="auto")
    ax.set_xticks(range(len(gcodes)), [n for n, _ in gcodes], rotation=20, ha="right")
    ax.set_yticks(range(len(tracks)), [n for n, _ in tracks])
    ax.set_xlabel("G-code the print is checked against"); ax.set_ylabel("camera video")
    for i in range(len(tracks)):
        for j in range(len(gcodes)):
            ok = p95[i, j] <= tol and cover[i, j] >= 0.9
            txt = "n/a" if np.isnan(p95[i, j]) else f"{p95[i, j]:.1f}" + ("\nPASS" if ok else "")
            ax.text(j, i, txt, ha="center", va="center", fontsize=9, fontweight="bold" if ok else "normal")
    fig.colorbar(im, ax=ax, label="p95 path deviation (mm, capped at 40)")
    ax.set_title(f"Camera vs G-code check (PASS = p95 <= {tol:g} mm and >= 90% path covered)", fontsize=10)
    fig.tight_layout(); fig.savefig(out / "matrix.png", dpi=130)
    print(f"Matrix saved to {out / 'matrix.png'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("track", help="Track the printer head in a video")
    s.add_argument("--video", required=True, type=Path)
    s.add_argument("--output", required=True, type=Path)
    s.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    s.add_argument("--device", default="cpu", help="cpu, mps or 0")
    s.set_defaults(func=cmd_track)

    s = sub.add_parser("calibrate", help="Fit camera->bed mapping from a known print")
    s.add_argument("--track", required=True, type=Path)
    s.add_argument("--gcode", required=True, type=Path)
    s.add_argument("--output", required=True, type=Path)
    s.add_argument("--mapping-model", default="pinhole_plus", choices=["pinhole", "pinhole_yaw", "pinhole_plus"])
    s.set_defaults(func=cmd_calibrate)

    for name, func in (("compare", cmd_compare), ("run", cmd_run)):
        s = sub.add_parser(name, help="Compare a print with its G-code" + (" (tracks first)" if name == "run" else ""))
        if name == "compare":
            s.add_argument("--track", required=True, type=Path)
            s.add_argument("--video", type=Path, help="Optional, adds a camera frame to the figure")
        else:
            s.add_argument("--video", required=True, type=Path)
            s.add_argument("--model", type=Path, default=DEFAULT_MODEL)
            s.add_argument("--device", default="cpu")
        s.add_argument("--gcode", required=True, type=Path)
        s.add_argument("--calibration", required=True, type=Path)
        s.add_argument("--output-dir", required=True, type=Path)
        s.add_argument("--tolerance", type=float, default=None,
                       help="mm; default = 2.5 x calibration held-out RMSE (min 5 mm)")
        s.add_argument("--label")
        s.set_defaults(func=func)

    s = sub.add_parser("matrix", help="Every video vs every G-code")
    s.add_argument("--track", action="append", required=True, metavar="NAME=CSV")
    s.add_argument("--gcode", action="append", required=True, metavar="NAME=GCODE")
    s.add_argument("--calibration", required=True, type=Path)
    s.add_argument("--output-dir", required=True, type=Path)
    s.add_argument("--tolerance", type=float, default=None)
    s.set_defaults(func=cmd_matrix)

    a = p.parse_args(argv)
    try:
        a.func(a)
    except (ValueError, FileNotFoundError) as error:
        p.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
