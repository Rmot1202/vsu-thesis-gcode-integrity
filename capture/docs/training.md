# Printer-head training

The detector uses one class, `printer_head` (class ID 0). Label the whole
visible printer head consistently, not just its yellow sticker. The old
warning-label dataset is retained as legacy data and is not automatically
converted.

Run commands from the repository root (`code/`). The local environment and
training requirements are:

```bash
capture/.venv/bin/python -m pip install \
  -r capture/backend/post_analysis/training/requirements.txt
```

## Prepare training and validation samples

Use distinct photos/videos for training and validation when possible. Keep the
generated dataset under `capture/outputs/training/`:

```bash
capture/.venv/bin/python capture/backend/post_analysis/training/prepare_dataset.py \
  --images-dir "/path/to/training/photos" \
  --dataset-dir capture/outputs/training/datasets/printer_head --split train

capture/.venv/bin/python capture/backend/post_analysis/training/prepare_dataset.py \
  --images-dir "/path/to/validation/photos" \
  --dataset-dir capture/outputs/training/datasets/printer_head --split val
```

For video, use `--video "/path/to/movie.mov"` instead of `--images-dir`, and
optionally set `--interval-seconds 0.5`. The annotation UI requires human
review: **A** draws a box, **N** saves a true negative, **S** skips, and **Q**
finishes. Never mark a missed visible head as a negative.

## Train

```bash
capture/.venv/bin/python capture/backend/post_analysis/training/train_yolo.py \
  --dataset-dir capture/outputs/training/datasets/printer_head \
  --epochs 80 --batch 16 --device cpu
```

On Apple Silicon, `--device mps` may be used. The base model at
`capture/assets/models/base/yolo11n.pt` is used when present; otherwise
Ultralytics falls back to `yolo11n.pt`. Base pretrained weights must be
fine-tuned on reviewed printer-head annotations before the custom class can be
detected. The training summary prints the exact `best_weights` path.

For assisted annotation, pass the trained `best.pt` to
`prepare_dataset.py` with `--model`; review every suggested box before
accepting it. Keep the validation split fixed across training rounds.

## Existing data and automation

The existing dataset is preserved in
`capture/outputs/training/legacy_dataset/`; its class is
`yellow_warning_label`, so review its labels before reusing them. The original
archive is in `capture/inputs/training_archives/`. It is not automatically
extracted or used.

The manual GitHub Actions workflow is
`.github/workflows/train-printer-head.yml`. It requires a configured runner
and reviewed dataset; it does not automatically deploy trained weights.
Large local datasets and generated training runs are kept under
`capture/outputs/training/`.
