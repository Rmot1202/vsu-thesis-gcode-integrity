# Print observation experiments

**Treat each run folder as a permanent experiment record once it has been created.**

Use one entry point from this `capture` directory:

```bash
source .venv/bin/activate
python experiment.py capture --view side_view --input 0
```

For the original angled/upward camera:

```bash
python experiment.py capture --view up_view --input 0
```

The workflow is: **capture → stop recording → optional reference prompt →
reconstruction/comparison → permanent experiment folder**. At the terminal
prompt, paste the original G-code path or press Enter to add it later.

New recordings are saved in:

```text
runs/side_view_2026-10-02_15-45-00_123456/
runs/up_view_2026-10-02_15-45-00_123456/
```

Each recording has its own view/date/time name; microseconds avoid collisions.
Videos, CSV, metadata, reference (if supplied), analysis G-code, images and
summaries all live in that folder. Recording V-stop may wait for the terminal
reference prompt; press Enter there before continuing to record.

## Navigation

- [Run commands](RUN_COMMANDS.md): copy-and-paste commands.
- [Workflow](WORKFLOW.md): capture lifecycle, startup cleanup, adding a reference.
- [Output guide](OUTPUTS.md): filenames, units, provenance and comparison status.
- [Reconstruction details](reconstruction/README.md): input contracts, algorithms,
  segmentation, layer evidence and optional processing.
- [Side-camera controls](side_camera/SIDE_VIEW.md).
- [Upward-camera calibration](upward_camera/CUSTOM_FIDUCIALS.md).

## Code structure

| Location | Responsibility |
| --- | --- |
| `experiment.py` | Unified capture, import, process and cleanup commands |
| `experiment_log.py` | Permanent folders, metadata, reference prompt, protected publishing |
| `tracking_common.py` | Shared CSRT, LED assistance and motion trigger |
| `side_camera/` | Side-view pixels/side-plane measurements and yellow-label tracking |
| `upward_camera/` | Printed-fiducial bed mapping and alternative HSV tracker |
| `reconstruction/` | Loading → alignment → segmentation/layers → conservative export |
| `legacy/` | Preserved one-off reference extractor; not the main workflow |
| `tests/` | Synthetic and video-file integration tests |

Shared files stay at this root; camera algorithms remain separate. The standard
side/up trackers start saving on confirmed motion after ROI initialization.
The alternative `up_auto_view` uses HSV pins and records immediately.

Startup removes only explicitly marked disposable tests in the selected run
root. Protected permanent experiments survive every startup, successful finish,
interruption, and processing failure. Existing historical camera captures remain
under `captures/`; use `experiment.py import` to bring one into the new workflow.
See WORKFLOW.md for the precise cleanup rule.

All reconstruction G-code is analysis-only, not printable. Side pixels cannot
produce printer XY. Temperature, extrusion, feedrate, and unknown layer/Z data
are not invented. No YOLO is required.
