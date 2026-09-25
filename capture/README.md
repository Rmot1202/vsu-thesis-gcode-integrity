# Camera-specific nozzle trackers

Run commands from this `capture` directory with the existing environment active:

```bash
source .venv/bin/activate
```

| Camera position | Script | Purpose | Default output root |
| --- | --- | --- | --- |
| Upward / angled bed view | `upward_camera/nozzle_grid_tracker.py` | Black fiducials, bed XY, CSRT/LED tracking | `captures/upward_camera/bed_view/` |
| Upward / angled bed view, alternative | `upward_camera/auto_nozzle_tracker.py` | HSV pin detection and automatic acquisition | `captures/upward_camera/auto_bed_view/` |
| Sideward view | `side_camera/side_nozzle_tracker.py` | Side-plane/pixel tracking, yellow labels and LEDs | `captures/side_camera/` |

“Upward” is the name for your original angled camera setup; it must still see
well-separated bed markers. It is not a claim that a camera looking straight
up or along the bed can use the same calibration.

## Upward camera: printed black fiducials

```bash
python upward_camera/nozzle_grid_tracker.py
```

Press S to select the head. Motion starts recording automatically. V stops and
disarms automatic restart; A re-arms; Q quits. Calibration and two/three-marker
fallback operate as before. See [fiducial guide](upward_camera/CUSTOM_FIDUCIALS.md).

## Sideward camera

```bash
python side_camera/side_nozzle_tracker.py --input 0
```

Press S, select the head including both yellow labels, SHIFT-click the tip,
then SPACE to resume. Movement starts recording automatically. V stops; A
re-arms; Q quits. See [side guide](side_camera/SIDE_VIEW.md).
Side-view coordinates are not printer bed XY or depth measurements.

## Alternative upward-camera tracker

```bash
python upward_camera/auto_nozzle_tracker.py --input 0 --csv trajectory.csv
```

This alternative records processed frames immediately. It uses HSV pins rather
than the custom black-block detector. Optional XY-only G-code requires measured
pin spacing; see [automatic tracker guide](upward_camera/AUTO_TRACKER.md).

## Shared files and outputs

`tracking_common.py` contains shared CSRT creation, LED detection, and motion
trigger settings. `requirements.txt` and `.venv/` remain at this root. Install
requirements with `python -m pip install -r requirements.txt` if needed.
Neither camera folder imports the other camera's tracker.

Recordings have dated session subfolders. Filenames remain `side_*`, `bed_*`,
or `auto_bed_*`, and new CSV/JSON data retains the `view_mode` field. Path defaults
are anchored to this capture root, regardless of terminal working directory.
`--save-dir` overrides still apply where supported.

Existing recordings identified by calibration metadata are moved into their
matching camera output tree without changing video or CSV contents. Unidentified
old recordings stay in their original locations under `captures/` and should
be treated as legacy/unclassified data. No recordings are deleted.
