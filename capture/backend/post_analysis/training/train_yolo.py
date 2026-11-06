#!/usr/bin/env python3
"""Train a small Ultralytics YOLO detector on prepared printer-head data."""
import argparse
import json
from pathlib import Path

DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[3] / 'assets' / 'models' / 'base' / 'yolo11n.pt'
DEFAULT_MODEL = str(DEFAULT_MODEL_PATH) if DEFAULT_MODEL_PATH.is_file() else 'yolo11n.pt'


def train(dataset_dir, model=DEFAULT_MODEL, epochs=80, image_size=640, batch=16,
          project=None, run_name='printer-head', device=None, min_map=None):
    if min_map is not None and not 0 <= min_map <= 1:
        raise ValueError('min_map must be in [0, 1]')
    dataset_dir = Path(dataset_dir).expanduser().resolve()
    data_yaml = dataset_dir / 'data.yaml'
    if not data_yaml.is_file():
        raise ValueError(f'YOLO dataset config is missing: {data_yaml}')
    if json.loads(data_yaml.read_text()).get('names') != ['printer_head']:
        raise ValueError('Expected a reviewed printer_head dataset; do not relabel warning-sticker boxes')
    train_images = list((dataset_dir / 'images' / 'train').glob('*.jpg'))
    validation_images = list((dataset_dir / 'images' / 'val').glob('*.jpg'))
    if not train_images or not validation_images:
        raise ValueError('Dataset needs at least one training and one validation image. Add more labeled frames or adjust the split.')
    try:
        from ultralytics import YOLO
    except ImportError as error:
        raise RuntimeError('YOLO training requires optional dependencies. Install backend/post_analysis/training/requirements.txt.') from error

    output_project = Path(project).expanduser().resolve() if project else dataset_dir / 'runs'
    detector = YOLO(model)
    results = detector.train(data=str(data_yaml), epochs=epochs, imgsz=image_size,
                             batch=batch, project=str(output_project), name=run_name,
                             device=device, pretrained=True)
    run_dir = Path(detector.trainer.save_dir)
    summary = {
        'model': model,
        'dataset': str(dataset_dir),
        'class_names': ['printer_head'],
        'epochs': epochs,
        'image_size': image_size,
        'batch': batch,
        'device': device or 'auto',
        'best_weights': str(run_dir / 'weights' / 'best.pt'),
        'last_weights': str(run_dir / 'weights' / 'last.pt'),
        'results_csv': str(run_dir / 'results.csv'),
        'metrics': getattr(results, 'results_dict', {}),
    }
    (run_dir / 'training_summary.json').write_text(json.dumps(summary, indent=2, default=float) + '\n')
    print(json.dumps(summary, indent=2, default=float))
    if min_map is not None:
        score = float(summary['metrics'].get('metrics/mAP50-95(B)', float('nan')))
        if not score >= min_map:
            raise RuntimeError(f'Validation mAP50-95 {score} did not meet {min_map}; candidate rejected')
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset-dir', type=Path, required=True)
    parser.add_argument('--model', default=DEFAULT_MODEL,
                        help='Ultralytics model/weights; uses the local YOLO11 nano file when available')
    parser.add_argument('--epochs', type=int, default=80)
    parser.add_argument('--image-size', type=int, default=640)
    parser.add_argument('--batch', type=int, default=16)
    parser.add_argument('--project', type=Path)
    parser.add_argument('--name', default='printer-head')
    parser.add_argument('--device', help='Optional Ultralytics device, e.g. mps, cpu, 0')
    parser.add_argument('--min-map', type=float, help='Fail if validation mAP50-95 is below this threshold')
    args = parser.parse_args(argv)
    train(args.dataset_dir, args.model, args.epochs, args.image_size, args.batch,
          args.project, args.name, args.device, args.min_map)


if __name__ == '__main__':
    main()
