# Run commands

Run commands from the `capture/` directory. The GUI is the recommended workflow:

```bash
source .venv/bin/activate
python application/gui.py
```

In the GUI, choose where to save the G-code, then select or drop a video (or
use a camera index). The original G-code is an optional comparison reference;
drop it into the reference field if available. Choose side or top view and
start camera capture. Click **Stop camera & Recode XY** to stop, reconstruct,
and export the calibrated XY path to the selected destination. Camera evidence
is retained under `outputs/runs/` by default. Terminal commands are optional
for this GUI workflow.

To create the environment if needed:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## CLI capture

Side view is the default in the GUI. Side tracking requires the trained model
and a validated side-to-bed calibration; see [side-camera setup](side-view.md).

```bash
python application/experiment.py capture --view side_view --input 0
```

For the top camera:

```bash
python application/experiment.py capture --view up_view --input 0
```

Provide a reference file in advance or suppress the terminal prompt:

```bash
python application/experiment.py capture --view up_view --input 0 \
  --reference inputs/references/model.gcode
python application/experiment.py capture --view up_view --input 0 \
  --no-reference-prompt
```

The CLI can also import an existing trajectory:

```bash
python application/experiment.py import --view side_view \
  --trajectory /path/to/side_trajectory.csv --no-reference-prompt
```

Process a saved run and optionally add an original reference:

```bash
python application/experiment.py process \
  --run outputs/runs/YOUR_RUN_FOLDER \
  --reference /path/to/model.gcode
```

## Direct camera tools

The camera modules can run directly when needed:

```bash
python cameras/side/side_nozzle_tracker.py --input 0
python cameras/top/nozzle_grid_tracker.py --input 0
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Test-run cleanup is available through `python application/experiment.py
cleanup-tests`. It only removes explicitly marked disposable test folders;
permanent records are protected. See [workflow](WORKFLOW.md) and
[output formats](OUTPUTS.md).
