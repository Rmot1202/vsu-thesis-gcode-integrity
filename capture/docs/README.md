# Project guide

Start the desktop app from the `capture/` directory:

```bash
python application/gui.py
```

The GUI defaults to side view and offers top view as an alternative. Side-view
XY requires a trained printer-head model and a validated side-to-bed
calibration. See [training](training.md), [side-camera setup](side-view.md),
and [top-camera calibration](top-camera-calibration.md).

## Folder map

| Folder | Contents |
| --- | --- |
| `application/` | Desktop GUI and command-line entry points |
| `backend/` | Capture lifecycle, reconstruction, YOLO analysis, and training code |
| `cameras/side/` | Side-view head tracking and printer XY mapping |
| `cameras/top/` | Top-view bed tracking and fiducial calibration |
| `inputs/` | Reference G-code, sample data, videos, and imported archives |
| `outputs/` | Camera runs, analyses, training datasets, and generated results |
| `assets/` | Model weights and CAD/project assets |
| `docs/` | All project Markdown documentation and archived run guides |
| `tests/` | Automated regression and integration tests |

## Documentation

- **[Camera vs G-code integrity check](integrity.md)** — track → calibrate → compare, results and side-by-side figures
- [Run commands](RUN_COMMANDS.md)
- [Workflow and retention](WORKFLOW.md)
- [Output formats and limitations](OUTPUTS.md)
- [Reconstruction pipeline](reconstruction.md)
- [Side camera](side-view.md)
- [Top camera](top-camera-calibration.md), [top tracker](auto-tracker.md),
  and [top-camera code notes](top-camera-code.md)
- [Printer-head training](training.md) and [asset locations](training-assets.md)
- [Post-analysis](post-analysis.md), [legacy tools](legacy.md), and
  [reference example](square-reference.md)

Permanent historical run documentation is kept in this folder as
`run_<run-name>_<document>.md`; these snapshots retain the original run-time
paths for provenance and are not current instructions. The corresponding run
data is in `outputs/runs/<run-name>/`; use the centralized guides above for
current commands and locations.
