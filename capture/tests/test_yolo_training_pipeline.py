import json
import sys
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from backend.post_analysis.training.train_yolo import train
from backend.post_analysis.training.prepare_dataset import YoloDatasetWriter, bbox_to_yolo, ModelProposer, image_samples, review_samples


class YoloTrainingDatasetTests(unittest.TestCase):
    def test_bbox_conversion_clips_to_image(self):
        values = bbox_to_yolo((-5, 10, 25, 30), 100, 100)
        np.testing.assert_allclose(values, (0.1, 0.25, 0.2, 0.3))

    def test_dataset_writer_saves_full_frame_label_and_crop(self):
        import cv2

        with tempfile.TemporaryDirectory() as temp:
            dataset = YoloDatasetWriter(temp, validation_fraction=0, seed=3)
            frame = np.zeros((80, 100, 3), np.uint8)
            frame[20:50, 30:70] = (0, 255, 255)
            result = dataset.save_sample(frame, 'side-run.mp4', 15, (30, 20, 40, 30))
            self.assertEqual(result['split'], 'train')
            self.assertTrue(result['image'].is_file())
            self.assertTrue(result['crop'].is_file())
            self.assertTrue(result['label'].read_text().startswith('0 '))
            self.assertTrue((Path(temp) / 'data.yaml').is_file())
            crop = cv2.imread(str(result['crop']))
            self.assertEqual(crop.shape[:2], (46, 56))

    def test_preserves_existing_wrong_class_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'data.yaml'
            original = json.dumps({'names': ['yellow_warning_label']})
            config.write_text(original)
            with self.assertRaises(ValueError):
                YoloDatasetWriter(temp)
            self.assertEqual(config.read_text(), original)

    def test_negative_and_duplicate_across_splits(self):
        with tempfile.TemporaryDirectory() as temp:
            frame = np.zeros((40, 50, 3), np.uint8)
            writer = YoloDatasetWriter(temp, 0)
            saved = writer.save_sample(frame, 'clip.mov', 3)
            self.assertEqual(saved['label'].read_text(), '')
            self.assertIsNone(saved['crop'])
            self.assertEqual(json.loads((Path(temp) / 'data.yaml').read_text())['names'], ['printer_head'])
            writer.validation_fraction = 1
            with self.assertRaises(FileExistsError):
                writer.save_sample(frame, 'clip.mov', 3)

    def test_photos_with_same_name_have_unique_ids(self):
        import cv2
        with tempfile.TemporaryDirectory() as temp:
            for subfolder in ('a', 'b'):
                folder = Path(temp) / subfolder
                folder.mkdir()
                cv2.imwrite(str(folder / 'photo.jpg'), np.zeros((30, 40, 3), np.uint8))
            samples = list(image_samples(Path(temp)))
            self.assertEqual(len(samples), 2)
            self.assertNotEqual(samples[0][0], samples[1][0])

    def test_predictions_filtered_sorted_and_clipped(self):
        class Tensor:
            def __init__(self, values):
                self.values = np.array(values)
            def cpu(self):
                return self
            def numpy(self):
                return self.values
        boxes = SimpleNamespace(xyxy=Tensor([[5, 5, 15, 15], [-2, 8, 60, 45]]),
                                conf=Tensor([0.4, 0.9]))
        model = SimpleNamespace(names={0: 'printer_head'},
                                predict=lambda *a, **kw: [SimpleNamespace(boxes=boxes)])
        with tempfile.TemporaryDirectory() as temp:
            weights = Path(temp) / 'best.pt'
            weights.touch()
            with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=lambda _: model)}):
                proposer = ModelProposer(weights)
                proposals = proposer(np.zeros((40, 50, 3), np.uint8))
                self.assertEqual(proposals[0][0], (0, 8, 50, 32))
                model.names = {0: 'yellow_warning_label'}
                with self.assertRaises(ValueError):
                    ModelProposer(weights)

    def test_review_accepts_prediction_but_skip_does_not_save(self):
        with tempfile.TemporaryDirectory() as temp:
            writer = YoloDatasetWriter(temp, 0)
            frame = np.zeros((40, 50, 3), np.uint8)
            samples = [('photo', 0, frame), ('photo', 1, frame)]
            with patch('cv2.namedWindow'), patch('cv2.imshow'), patch('cv2.destroyAllWindows'), \
                 patch('cv2.waitKey', side_effect=[ord('1'), ord('s')]):
                counts = review_samples(samples, writer, lambda _: [((5, 5, 15, 20), .9)])
            self.assertEqual(counts['positive_frames'], 1)
            self.assertEqual(counts['skipped_frames'], 1)
            self.assertEqual(len(list((Path(temp) / 'images/train').glob('*.jpg'))), 1)

    def test_training_uses_actual_run_directory_and_metric_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            writer = YoloDatasetWriter(root / 'dataset', 0)
            frame = np.zeros((40, 50, 3), np.uint8)
            writer.save_sample(frame, 'train', 0, (5, 5, 15, 20))
            writer.validation_fraction = 1
            writer.save_sample(frame, 'val', 0, (5, 5, 15, 20))
            actual_run = root / 'runs' / 'printer-head2'
            actual_run.mkdir(parents=True)
            model = SimpleNamespace(trainer=SimpleNamespace(save_dir=actual_run),
                train=lambda **kwargs: SimpleNamespace(results_dict={'metrics/mAP50-95(B)': .6}))
            with patch.dict(sys.modules, {'ultralytics': SimpleNamespace(YOLO=lambda _: model)}), \
                 patch('builtins.print'):
                result = train(writer.dataset_dir, min_map=.5)
                self.assertEqual(result['best_weights'], str(actual_run / 'weights/best.pt'))
                self.assertTrue((actual_run / 'training_summary.json').exists())
                with self.assertRaises(RuntimeError):
                    train(writer.dataset_dir, min_map=.7)


if __name__ == '__main__':
    unittest.main()