# Run commands

Run from your project's `capture` directory:

```bash
source .venv/bin/activate
```

If creating a new environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Side camera

```bash
python experiment.py capture --view side_view --input 0
```

S selects the head including yellow labels, SHIFT-click sets the physical tip,
SPACE resumes. Movement starts recording. V stops (and disarms); A re-arms;
Q quits. After stopping, answer the reference prompt in the terminal or press
Enter. Each recording remains saved in `runs/side_view_date_time/`.

Optional side-plane calibration (use the actual reference dimensions):

```bash
python experiment.py capture --view side_view --input 0 \
  --reference-width-mm 100 --reference-height-mm 60
```

Press C and click TL/TR/BR/BL on that vertical reference. Side-plane coordinates
are not printer XY or depth.

## Upward / angled bed camera

```bash
python experiment.py capture --view up_view --input 0
```

S selects the nozzle/head. Movement starts saving. V stops; A re-arms;
R resets calibration; D toggles the mask; Q quits.

## Give the reference in advance, or suppress the prompt

```bash
python experiment.py capture --view side_view --input 0 \
  --reference '/path/to/model.gcode'
```

```bash
python experiment.py capture --view up_view --input 0 --no-reference-prompt
```

No reference still produces conservative reconstruction outputs; comparison is
marked pending. Use the next command to add it later.

## Add a reference or process an existing permanent run

Replace the example run name with the folder printed by your recording:

```bash
python experiment.py process \
  --run runs/side_view_2026-10-02_15-45-00_123456 \
  --reference '/path/to/model.gcode' --no-reference-prompt
```

Results remain in that same folder. Existing capture files are retained, previous
analysis is stored in `analysis_history/`, and replaced inputs in `input_history/`.

## Import an existing trajectory (reference-free)

```bash
python experiment.py import --view side_view \
  --trajectory '/path/to/side_trajectory.csv' --no-reference-prompt
```

This copies the CSV into a new permanent experiment. It does not invent or copy
missing video files. For old bed-coordinate data use `--view up_view`.

## Optional layer/state evidence and denoising

```bash
python experiment.py process --run runs/YOUR_RUN_FOLDER \
  --states '/path/to/states.csv' --per-layer-plots \
  --smooth-alpha 0.35 --no-reference-prompt
```

State CSV rows match `frame_index`/`frame`, with optional layer_index, z_mm,
motion_state, confidence and provenance. See reconstruction/README.md. Settings
are recorded in the summary. Reprocessing uses defaults for flags omitted from
the new command; copied evidence/reference files remain available in the run.

## Alternative HSV-pin tracker

```bash
python experiment.py capture --view up_auto_view --input 0 --no-reference-prompt
```

This differs from the printed-black-fiducial tracker. It records immediately.
Its current warped-bed coordinates are pixels, so automatic reconstruction
conservatively uses image coordinates unless justified metric input is supplied.

## Video file input and custom run root

```bash
python experiment.py capture --view side_view \
  --input '/path/to/printer.mp4' --runs-dir '/path/to/experiments'
```

## Test cleanup and validation

```bash
python experiment.py cleanup-tests
python -m unittest discover -s tests -v
```

Cleanup skips permanent records and unmarked folders. Only a folder explicitly
marked as a disposable test is deleted. See WORKFLOW.md.

The view scripts remain directly runnable, using the same logging workflow:

```bash
python side_camera/side_nozzle_tracker.py --input 0
python upward_camera/nozzle_grid_tracker.py --input 0
```
