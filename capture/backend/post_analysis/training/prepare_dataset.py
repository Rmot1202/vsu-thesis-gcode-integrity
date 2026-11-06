#!/usr/bin/env python3
"""Review photos or video frames to build a printer-head YOLO dataset."""
import argparse
import csv
import json
import random
import re
from pathlib import Path

import cv2
import numpy as np

CLASS_NAME = 'printer_head'


def bbox_to_yolo(bbox, image_width, image_height):
    """Convert an xywh pixel box to normalized YOLO center-x/y, width, height."""
    x, y, width, height = map(float, bbox)
    left = max(0.0, min(x, image_width))
    top = max(0.0, min(y, image_height))
    right = max(left, min(x + width, image_width))
    bottom = max(top, min(y + height, image_height))
    if right <= left or bottom <= top:
        raise ValueError('Bounding box must overlap the image with positive area')
    box_width = right - left
    box_height = bottom - top
    center_x = (left + right) / (2 * image_width)
    center_y = (top + bottom) / (2 * image_height)
    return center_x, center_y, box_width / image_width, box_height / image_height


class YoloDatasetWriter:
    """Write labeled full frames and review crops in standard YOLO layout."""
    def __init__(self, dataset_dir, validation_fraction=0.2, seed=42, crop_padding=8):
        if not 0 <= validation_fraction < 1:
            raise ValueError('validation_fraction must be in [0, 1)')
        self.dataset_dir = Path(dataset_dir).expanduser().resolve()
        self.validation_fraction = validation_fraction
        self.random = random.Random(seed)
        self.crop_padding = max(0, int(crop_padding))
        config_path = self.dataset_dir / 'data.yaml'
        if config_path.exists():
            try:
                names = json.loads(config_path.read_text())['names']
            except (ValueError, KeyError) as error:
                raise ValueError('Use a new dataset directory; existing config cannot be verified') from error
            if names != [CLASS_NAME]:
                raise ValueError('Use a new printer_head dataset; existing boxes belong to another class')
        for split in ('train', 'val'):
            (self.dataset_dir / 'images' / split).mkdir(parents=True, exist_ok=True)
            (self.dataset_dir / 'labels' / split).mkdir(parents=True, exist_ok=True)
        (self.dataset_dir / 'crops').mkdir(exist_ok=True)
        self._write_data_yaml()

    def _write_data_yaml(self):
        config = {
            'path': str(self.dataset_dir),
            'train': 'images/train',
            'val': 'images/val',
            'names': [CLASS_NAME],
        }
        # JSON is valid YAML and avoids a separate YAML dependency.
        (self.dataset_dir / 'data.yaml').write_text(json.dumps(config, indent=2) + '\n')

    def save_sample(self, frame, video_id, frame_index, bbox=None):
        height, width = frame.shape[:2]
        split = 'val' if self.random.random() < self.validation_fraction else 'train'
        safe_video = re.sub(r'[^A-Za-z0-9_-]+', '_', Path(video_id).stem).strip('_') or 'video'
        stem = f'{safe_video}_{int(frame_index):08d}'
        image_path = self.dataset_dir / 'images' / split / f'{stem}.jpg'
        label_path = self.dataset_dir / 'labels' / split / f'{stem}.txt'
        crop_path = self.dataset_dir / 'crops' / f'{stem}.jpg'
        if any((self.dataset_dir / folder / part / f'{stem}.{suffix}').exists()
               for part in ('train', 'val')
               for folder, suffix in (('images', 'jpg'), ('labels', 'txt'))) or crop_path.exists():
            raise FileExistsError(f'Dataset sample already exists: {stem}')
        normalized = bbox_to_yolo(bbox, width, height) if bbox is not None else None
        if not cv2.imwrite(str(image_path), frame):
            raise OSError(f'Could not write image: {image_path}')

        if bbox is None:
            label_path.write_text('')
        else:
            label_path.write_text('0 ' + ' '.join(f'{value:.8f}' for value in normalized) + '\n')
            x, y, box_width, box_height = map(int, bbox)
            padding = self.crop_padding
            left = max(0, x - padding)
            top = max(0, y - padding)
            right = min(width, x + box_width + padding)
            bottom = min(height, y + box_height + padding)
            if not cv2.imwrite(str(crop_path), frame[top:bottom, left:right]):
                raise OSError(f'Could not write label crop: {crop_path}')

        manifest = self.dataset_dir / 'annotations.csv'
        write_header = not manifest.exists()
        with manifest.open('a', newline='') as csv_file:
            writer = csv.writer(csv_file)
            if write_header:
                writer.writerow(['image', 'label', 'split', 'video', 'frame_index',
                                 'class_name', 'bbox_xywh_pixels'])
            writer.writerow([str(image_path.relative_to(self.dataset_dir)),
                             str(label_path.relative_to(self.dataset_dir)), split,
                             video_id, frame_index,
                             CLASS_NAME if bbox is not None else '',
                             json.dumps(list(map(int, bbox))) if bbox is not None else ''])
        return {'split': split, 'image': image_path, 'label': label_path,
                'crop': crop_path if bbox is not None else None, 'bbox_yolo': normalized}


class ModelProposer:
    """Load custom printer-head weights once; never save predictions automatically."""
    def __init__(self, weights, confidence=0.25, device=None):
        if not 0 < confidence <= 1:
            raise ValueError('confidence must be in (0, 1]')
        weights = Path(weights).expanduser().resolve()
        if not weights.is_file():
            raise ValueError(f'Model weights do not exist: {weights}')
        from ultralytics import YOLO
        self.model = YOLO(str(weights))
        names = self.model.names
        items = names.items() if isinstance(names, dict) else enumerate(names)
        self.classes = [index for index, name in items if name == CLASS_NAME]
        if not self.classes:
            raise ValueError('Model must contain the printer_head class; train custom weights first')
        self.confidence = confidence
        self.device = device

    def __call__(self, frame):
        result = self.model.predict(frame, conf=self.confidence, classes=self.classes,
                                    device=self.device, verbose=False)[0]
        if result.boxes is None:
            return []
        boxes = result.boxes.xyxy.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        order = np.argsort(-scores)
        height, width = frame.shape[:2]
        proposals = []
        for index in order:
            x1, y1, x2, y2 = boxes[index]
            left, top = max(0, int(np.floor(x1))), max(0, int(np.floor(y1)))
            right, bottom = min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2)))
            if right > left and bottom > top:
                proposals.append(((left, top, right - left, bottom - top), float(scores[index])))
        return proposals[:9]


def _preview(frame, proposals, frame_index, total_sampled):
    display = frame.copy()
    for index, (box, score) in enumerate(proposals, 1):
        x, y, width, height = box
        cv2.rectangle(display, (x, y), (x + width, y + height), (0, 255, 255), 2)
        cv2.putText(display, f'{index}: printer head {score:.0%}', (x, max(18, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 255, 255), 2)
    lines = [f'Image/frame {frame_index} ({total_sampled} saved)',
             '1-9: accept box | A: draw/correct printer head | N: negative | S: skip | Q: finish']
    for row, line in enumerate(lines):
        origin = (12, 25 + row * 26)
        cv2.putText(display, line, origin, cv2.FONT_HERSHEY_SIMPLEX, .55, (0, 0, 0), 3)
        cv2.putText(display, line, origin, cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
    return display


def review_samples(samples, dataset, proposer=None, max_frames=None):
    """Each sample has one printer head; only human-confirmed boxes are saved."""
    if max_frames is not None and max_frames <= 0:
        raise ValueError('max_frames must be positive')
    window = 'Printer head YOLO annotation'
    counts = dict(sampled_frames=0, positive_frames=0, negative_frames=0, skipped_frames=0)
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    try:
        for source, frame_index, frame in samples:
            proposals = proposer(frame) if proposer else []
            while True:
                cv2.imshow(window, _preview(frame, proposals, frame_index, counts['sampled_frames']))
                key = cv2.waitKey(0) & 255
                if key == ord('q'):
                    return counts
                box = None
                if key == ord('a'):
                    box = cv2.selectROI(window, frame, fromCenter=False, showCrosshair=True)
                    cv2.setMouseCallback(window, lambda *_args: None)
                    if box[2] <= 0 or box[3] <= 0:
                        continue
                elif ord('1') <= key <= ord('9'):
                    index = key - ord('1')
                    if index >= len(proposals):
                        continue
                    box = proposals[index][0]
                elif key not in (ord('n'), ord('s')):
                    continue
                if key == ord('s'):
                    counts['skipped_frames'] += 1
                else:
                    dataset.save_sample(frame, source, frame_index, box)
                    counts['sampled_frames'] += 1
                    counts['positive_frames' if box is not None else 'negative_frames'] += 1
                break
            if max_frames is not None and counts['sampled_frames'] >= max_frames:
                break
    finally:
        cv2.destroyAllWindows()
    return counts


def video_samples(video_path, interval_seconds):
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise ValueError(f'Cannot open video: {video_path}')
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not np.isfinite(fps) or fps <= 0:
            fps = 30.0
        step = max(1, round(interval_seconds * fps))
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if index % step == 0:
                yield video_path.name, index, frame
            index += 1
    finally:
        capture.release()


def image_samples(directory):
    import hashlib
    extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
    paths = sorted(p for p in directory.rglob('*') if p.suffix.lower() in extensions)
    if not paths:
        raise ValueError(f'No supported photos in {directory}')
    for path in paths:
        frame = cv2.imread(str(path))
        if frame is None:
            raise ValueError(f'Cannot read photo: {path}')
        # Stable relative-path ID avoids collisions between identically named photos.
        digest = hashlib.sha256(str(path.relative_to(directory)).encode()).hexdigest()[:12]
        yield f'{path.stem}_{digest}', 0, frame


def prepare_video(video_path, dataset_dir, interval_seconds=1.0, validation_fraction=0.2,
                  seed=42, max_frames=None, model=None, confidence=0.25, device=None,
                  split=None):
    return prepare_source(video_path, dataset_dir, False, interval_seconds,
                          validation_fraction, seed, max_frames, model, confidence, device, split)


def prepare_source(source, dataset_dir, photos=False, interval_seconds=1.0,
                   validation_fraction=0.2, seed=42, max_frames=None, model=None,
                   confidence=0.25, device=None, split=None):
    source = Path(source).expanduser().resolve()
    if not (source.is_dir() if photos else source.is_file()):
        raise ValueError(f'Source does not exist: {source}')
    if not np.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError('interval_seconds must be finite and positive')
    destination = Path(dataset_dir).expanduser().resolve()
    if photos and (destination == source or source in destination.parents):
        raise ValueError('Keep the dataset outside the input photo folder')
    proposer = ModelProposer(model, confidence, device) if model else None
    dataset = YoloDatasetWriter(dataset_dir, validation_fraction, seed)
    if split is not None:
        # Explicit split keeps separate recordings in a fixed validation set.
        dataset.validation_fraction = 1.0 if split == 'val' else 0.0
    samples = image_samples(source) if photos else video_samples(source, interval_seconds)
    try:
        counts = review_samples(samples, dataset, proposer, max_frames)
    finally:
        samples.close()
    summary = dict(source=str(source), dataset_dir=str(dataset.dataset_dir), **counts,
                   class_name=CLASS_NAME, model=str(model) if model else None,
                   sample_interval_seconds=None if photos else interval_seconds,
                   split=split or 'random', validation_fraction=dataset.validation_fraction)
    summary_path = dataset.dataset_dir / 'preparation_summary.json'
    summaries = json.loads(summary_path.read_text()).get('sessions', []) if summary_path.exists() else []
    summaries.append(summary)
    summary_path.write_text(json.dumps({'class_names': [CLASS_NAME], 'sessions': summaries}, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--video', type=Path)
    source.add_argument('--images-dir', type=Path, help='Recursively review photos')
    parser.add_argument('--dataset-dir', type=Path, required=True)
    parser.add_argument('--interval-seconds', type=float, default=1.0)
    parser.add_argument('--validation-fraction', type=float, default=0.2)
    parser.add_argument('--split', choices=['train', 'val'], help='Assign this source to a fixed split')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--max-frames', type=int)
    parser.add_argument('--model', type=Path, help='Trained printer_head best.pt for suggestions')
    parser.add_argument('--confidence', type=float, default=0.25)
    parser.add_argument('--device', help='Inference device: mps, cpu, or 0')
    args = parser.parse_args(argv)
    prepare_source(args.images_dir or args.video, args.dataset_dir, bool(args.images_dir),
                   args.interval_seconds, args.validation_fraction, args.seed, args.max_frames,
                   args.model, args.confidence, args.device, args.split)


if __name__ == '__main__':
    main()
