import csv
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reconstruction.pipeline import parser,run
from reconstruction.ingest import load_trajectory
from reconstruction.reference import load_reference
from reconstruction.processing import clean,segment,organize_layers
from reconstruction.model import Point
from reconstruction.alignment import align


class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def csv(self,name,fields,rows):
        path=self.root/name
        with path.open('w',newline='') as f:
            writer=csv.writer(f);writer.writerow(fields);writer.writerows(rows)
        return path
    def fixture(self):
        return self.csv('obs.csv',['frame_index','time_seconds','x_mm','y_mm','layer_index','z_mm','motion_state'],
                        [[0,0,10,20,0,.2,'perimeter'],[1,.1,20,20,0,.2,'perimeter'],
                         [2,.2,'','',0,.2,'unknown'],[3,.3,40,45,0,.2,'infill'],[4,.4,50,45,0,.2,'infill'],
                         [5,.5,10,20,2,.6,'perimeter'],[6,.6,20,20,2,.6,'perimeter']])
    def args(self,csv,*extra):return parser().parse_args(['--trajectory',str(csv),'--output-dir',str(self.root/'out'),*extra])
    def test_multilayer_islands_and_unknown(self):
        summary=run(self.args(self.fixture(),'--per-layer-plots'))
        self.assertEqual(summary['observed_layer_order'],['0','2'])
        self.assertEqual(summary['segments'],3)
        output=(self.root/'out/reconstructed_layered_path_only.gcode').read_text()
        self.assertIn(';LAYER:2',output);self.assertNotIn(';LAYER:1\n',output)
        self.assertEqual(output.count('\nG0 '),3)
        for line in output.splitlines():
            if line.startswith('G'):self.assertNotRegex(line,r'\b[EFSMT]')
        self.assertTrue((self.root/'out/layer_2.png').is_file())
    def test_side_pixels_never_export_xy(self):
        path=self.csv('side.csv',['frame_index','elapsed_seconds','image_u_px','image_v_px','view_mode'],[[0,0,20,10,'side_view'],[1,1,30,10,'side_view']])
        summary=run(self.args(path))
        self.assertFalse(summary['bed_xy_commands_available'])
        self.assertNotIn('\nG1 ',(self.root/'out/reconstructed_path_only.gcode').read_text())
    def test_side_rejects_bed_promotion(self):
        path=self.csv('side.csv',['x_mm','y_mm','view_mode'],[[0,0,'side_view']])
        with self.assertRaises(ValueError):load_trajectory(path)
    def test_state_join_and_missing_layers(self):
        path=self.csv('a.csv',['frame_index','x_mm','y_mm'],[[7,1,2],[8,2,3]])
        states=self.csv('states.csv',['frame_index','layer_index','motion_state','confidence'],[[8,5,'travel',.8]])
        t=load_trajectory(path,states=states)
        self.assertIsNone(t.points[0].layer);self.assertEqual(t.points[1].layer,'5')
    def test_homography(self):
        path=self.csv('a.csv',['image_x','image_y'],[[3,4]])
        meta=self.root/'cal.json';meta.write_text(json.dumps({'coordinate_space':'bed_mm','image_to_bed_homography':[[2,0,10],[0,3,20],[0,0,1]]}))
        t=load_trajectory(path,calibration=meta);self.assertEqual(t.points[0].xy,(16,32))
    def test_gaps_and_opt_in_interpolation(self):
        p=[Point('0',0,(0,0)),Point('1',.1),Point('2',.2,(2,0))]
        self.assertEqual(len(segment(p,1)),2)
        counts=clean(p,10,0,1,1)
        self.assertEqual(counts['interpolated'],1);self.assertEqual(p[1].xy,(1,0))
        self.assertIn('interpolated_linear',p[1].provenance)
    def test_spike(self):
        p=[Point('0',0,(0,0)),Point('1',.1,(100,200)),Point('2',.2,(1,0))]
        self.assertEqual(clean(p,10,0,0,1)['outliers'],1);self.assertIsNone(p[1].xy)
    def test_layers_from_z_ignore_hops(self):
        p=[Point('0',xy=(0,0),z=.2,state='print'),Point('1',xy=(1,0),z=1,state='travel'),Point('2',xy=(2,0),z=.2,state='print'),Point('3',xy=(2,1),z=.4,state='print')]
        organize_layers(p,True);self.assertEqual([x.layer for x in p],['0',None,'0','1'])
    def test_reference_modes_and_gaps(self):
        f=self.root/'ref.gcode';f.write_text('G21\nG90\nM82\nG0 X0 Y0 Z0.2\n;LAYER:0\nG1 X10 E1\nG0 X30\nG1 Y10 E2\n;LAYER:2\nG0 Z0.6\nM83\nG91\nG1 X5 E1\n')
        t=load_reference(f);segs=segment(t.points,float('inf'))
        self.assertEqual(len(segs),3)
        self.assertEqual(segs[-1].points[-1].xy,(35,10))
        self.assertEqual(segs[-1].layer,'2')
    def test_reference_arc(self):
        f=self.root/'arc.gcode';f.write_text('G0 X10 Y0 Z0.2\nM83\nG3 X0 Y10 I-10 J0 E1\n')
        t=load_reference(f)
        self.assertGreater(len(t.points),10)
        self.assertAlmostEqual(t.points[-1].xy[0],0)
    def test_reference_rejects_fractional_mode(self):
        f=self.root/'bad.gcode';f.write_text('G90.1\nG0 X0 Y0\n')
        with self.assertRaises(ValueError):load_reference(f)
    def test_low_confidence_and_smoothing(self):
        p=self.csv('low.csv',['frame_index','time_seconds','x_mm','y_mm','confidence'],
                   [[0,0,0,0,1],[1,.1,2,0,1],[2,.2,99,99,.1],[3,.3,10,0,1]])
        summary=run(self.args(p,'--min-confidence','.5','--smooth-alpha','.5'))
        self.assertEqual(summary['cleanup']['low_confidence'],1)
        with (self.root/'out/reconstructed_points.csv').open() as f:rows=list(csv.DictReader(f))
        self.assertEqual(float(rows[1]['x']),1.)
        self.assertEqual(float(rows[-1]['x']),10.) # EMA resets across gap
        self.assertIn('ema_smoothed',rows[1]['provenance'])
    def test_reference_assisted_does_not_fill_layers(self):
        p=self.csv('unknown.csv',['frame_index','x_mm','y_mm'],[[0,0,0],[1,1,0]])
        ref=self.root/'r.gcode';ref.write_text('G0 X0 Y0\n;LAYER:5\nG1 X1 E1\n')
        summary=run(self.args(p,'--reference',str(ref)))
        self.assertEqual(summary['reference_layer_order'],['5'])
        self.assertEqual(summary['observed_layer_order'],[])
        self.assertIn(';LAYER:UNKNOWN',(self.root/'out/reconstructed_path_only.gcode').read_text())
    def test_rigid_registration(self):
        p=self.csv('a.csv',['x_mm','y_mm'],[[0,0],[1,0]])
        config=self.root/'align.json';config.write_text(json.dumps({'source_points':[[0,0],[1,0]],'target_points':[[10,20],[10,21]]}))
        t=load_trajectory(p);align(t,config,False)
        self.assertAlmostEqual(t.points[1].xy[0],10);self.assertAlmostEqual(t.points[1].xy[1],21)
    def test_archive_managed_outputs(self):
        args=self.args(self.fixture());run(args);run(args)
        self.assertEqual(len(list((self.root/'out_archive').iterdir())),1)
        (self.root/'out/user_file.txt').write_text('keep')
        with self.assertRaises(ValueError):run(args)
        self.assertEqual((self.root/'out/user_file.txt').read_text(),'keep')
    def test_unmanaged_output_is_preserved(self):
        (self.root/'out').mkdir();(self.root/'out/old.txt').write_text('keep')
        with self.assertRaises(ValueError):run(self.args(self.fixture()))
        self.assertTrue((self.root/'out/old.txt').exists())
    def test_alignment_cannot_promote_pixels(self):
        p=self.csv('pixels.csv',['image_x','image_y'],[[0,0]])
        with self.assertRaises(ValueError):align(load_trajectory(p),self.root/'nonexistent',False)


if __name__=='__main__':unittest.main()
