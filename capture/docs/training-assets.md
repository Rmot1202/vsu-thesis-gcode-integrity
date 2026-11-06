# Asset locations

Imported files and generated training resources are kept outside the project
root:

- `capture/assets/models/base/yolo11n.pt`: optional local pretrained YOLO base
  weights used automatically by training when present.
- `capture/inputs/training_archives/`: preserved source dataset archives.
- `capture/inputs/training_videos/`: source videos available for training.
- `capture/outputs/training/`: prepared datasets, training runs, and trained
  weights.

The archive is not extracted automatically. Review the labels and dataset
layout before using it. Large archives and model weights are excluded from
version control.
