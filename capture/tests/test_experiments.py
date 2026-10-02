import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
from experiment_log import Experiment,startup_cleanup,process_experiment,MARKER,TEST_MARKER
from side_camera.side_nozzle_tracker import RedStripTracker


class ExperimentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def session(self):
        s=Experiment.create(self.root,'side_view',{})
        with (s.folder/'trajectory.csv').open('w',newline='') as f:
            w=csv.writer(f);w.writerow(['frame_index','elapsed_seconds','image_u_px','image_v_px','view_mode'])
            w.writerows([[0,0,20,30,'side_view'],[1,.2,25,32,'side_view']])
        s.update(capture_status='finished');return s
    def test_cleanup_preserves_permanent_and_unmarked(self):
        s=self.session();test=self.root/'old_test';test.mkdir()
        (test/TEST_MARKER).write_text(json.dumps({'kind':'print_capture_test_run','delete_on_startup':True}))
        user=self.root/'user_files';user.mkdir();(user/'keep').write_text('keep')
        self.assertEqual(startup_cleanup(self.root),['old_test'])
        self.assertTrue(s.folder.exists());self.assertTrue(user.exists())
        self.assertEqual(startup_cleanup(self.root),[])
    def test_process_same_folder_and_add_reference(self):
        s=self.session();before=(s.folder/'trajectory.csv').read_bytes()
        process_experiment(s,prompt=False)
        self.assertEqual(s.metadata['comparison_status'],'pending')
        ref=self.root/'ref.gcode';ref.write_text('G0 X0 Y0\n;LAYER:0\nG1 X10 E1\n')
        process_experiment(s,ref,prompt=False)
        self.assertTrue((s.folder/'original_reference.gcode').exists())
        self.assertEqual(s.metadata['comparison_status'],'limited_coordinate_evidence')
        self.assertEqual(before,(s.folder/'trajectory.csv').read_bytes())
        self.assertTrue(list((s.folder/'analysis_history').iterdir()))
        g=(s.folder/'reconstructed_path_only.gcode').read_text()
        self.assertIn('; VIEW:side_view',g);self.assertNotIn('\nG1 ',g)
        self.assertTrue((s.folder/'comparison_summary.txt').exists())
    def test_processing_error_preserves_capture(self):
        s=self.session();ref=self.root/'bad.gcode';ref.write_text('G90.1\n')
        with self.assertRaises(ValueError):process_experiment(s,ref,prompt=False)
        self.assertTrue((s.folder/'trajectory.csv').exists());self.assertTrue((s.folder/MARKER).exists())
        self.assertEqual(s.metadata['processing_status'],'failed')
    def test_name_and_retention_across_startups(self):
        a=self.session();b=self.session()
        self.assertNotEqual(a.folder,b.folder);self.assertRegex(a.folder.name,r'^side_view_\d{4}-\d{2}-\d{2}_')
        startup_cleanup(self.root);self.assertTrue(a.folder.exists());self.assertTrue(b.folder.exists())
    def test_reference_prompt_and_pending(self):
        s=self.session()
        ref=self.root/'original.gcode';ref.write_text('G0 X0 Y0\nG1 X5 E1\n')
        with patch('sys.stdin.isatty',return_value=True),patch('builtins.input',return_value=str(ref)) as prompt:
            process_experiment(s,prompt=True)
            prompt.assert_called_once()
        self.assertTrue((s.folder/'original_reference.gcode').exists())
    def test_active_run_cannot_be_processed(self):
        s=self.session();s.update(capture_status='recording')
        with self.assertRaises(ValueError):process_experiment(s,prompt=False)
        self.assertTrue((s.folder/'trajectory.csv').exists())

    def test_red_strip_tracks_tip_offset_across_hue_wrap_after_csrt_loss(self):
        tracker=RedStripTracker()
        frame=np.zeros((120,160,3),np.uint8)
        cv2.rectangle(frame,(30,30),(70,45),(0,0,255),-1)
        cv2.rectangle(frame,(120,30),(150,45),(0,0,255),-1)
        tip=np.array([50.,70.])
        self.assertTrue(tracker.calibrate(frame,[65.,37.5],tip))
        np.testing.assert_allclose(tracker.update(frame),tip)
        moved_hsv=np.zeros((120,160,3),np.uint8)
        cv2.rectangle(moved_hsv,(40,35),(80,50),(175,255,255),-1)
        cv2.rectangle(moved_hsv,(120,30),(150,45),(175,255,255),-1)
        moved=cv2.cvtColor(moved_hsv,cv2.COLOR_HSV2BGR)
        estimated=tracker.update(moved)
        np.testing.assert_allclose(estimated,[60.,75.])

    def test_red_strip_calibration_reports_missing_candidates(self):
        tracker=RedStripTracker()
        frame=np.zeros((120,160,3),np.uint8)
        self.assertFalse(tracker.calibrate(frame,[50.,40.],[50.,70.]))
        self.assertIn('No red rectangle candidates',tracker.selection_error)

    def test_recorders_end_to_end(self):
        # Real video capture/writers; only user interaction and ROI selection are simulated.
        video=self.root/'input.mp4'
        writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),10,(320,240))
        self.assertTrue(writer.isOpened())
        for i in range(6):writer.write(np.full((240,320,3),100+i,np.uint8))
        writer.release()
        base=Path(__file__).resolve().parents[1]
        for view,relative in [('side_view','side_camera/side_nozzle_tracker.py'),('up_view','upward_camera/nozzle_grid_tracker.py'),('up_auto_view','upward_camera/auto_nozzle_tracker.py')]:
            spec=importlib.util.spec_from_file_location(view,base/relative);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            args=['--input',str(video),'--runs-dir',str(self.root/view),'--no-reference-prompt']
            if view=='up_auto_view':args+=['--headless','--manual']
            with patch.object(cv2,'namedWindow'),patch.object(cv2,'setMouseCallback'),patch.object(cv2,'imshow'),patch.object(cv2,'destroyAllWindows'),patch.object(cv2,'getWindowProperty',return_value=1),patch.object(cv2,'waitKey',side_effect=[ord('v'),-1,ord('v'),ord('q')]):
                module.main(args)
            folders=[p for p in (self.root/view).iterdir() if p.is_dir()]
            self.assertEqual(len(folders),1)
            folder=folders[0]
            for name in ['raw_video.mp4','annotated_video.mp4','trajectory.csv','metadata.json','reconstructed_path_only.gcode','comparison_summary.txt','README.md','WORKFLOW.md','OUTPUTS.md']:
                self.assertTrue((folder/name).is_file(),(view,name))
            meta=json.loads((folder/'metadata.json').read_text())
            self.assertEqual(meta['capture_status'],'finished');self.assertEqual(meta['processing_status'],'complete')
            for name in ['raw_video.mp4','annotated_video.mp4']:
                cap=cv2.VideoCapture(str(folder/name));ok,_=cap.read();cap.release();self.assertTrue(ok)


if __name__=='__main__':unittest.main()
