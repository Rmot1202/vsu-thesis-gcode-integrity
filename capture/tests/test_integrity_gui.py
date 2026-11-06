import json
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from application.integrity_tab import (build_calibrate_commands, build_compare_command, cached_track,
                                       default_output_dir, remember_track)


class IntegrityTabLogicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_compare_uses_saved_track_and_skips_yolo(self):
        cmd = build_compare_command("v.mov", "g.gcode", "cal.json", "out", track="t.csv", python="py")
        self.assertEqual(cmd[2], "compare")
        self.assertIn("t.csv", cmd)
        self.assertNotIn("--model", cmd)

    def test_compare_without_track_runs_tracking(self):
        cmd = build_compare_command("v.mov", "g.gcode", "cal.json", "out", model="m.pt", tolerance=8, python="py")
        self.assertEqual(cmd[2], "run")
        self.assertEqual(cmd[cmd.index("--model") + 1], "m.pt")
        self.assertEqual(cmd[cmd.index("--tolerance") + 1], "8")

    def test_calibration_tracks_then_calibrates(self):
        cmds = build_calibrate_commands("rect.mov", "rect.gcode", "cal.json", python="py")
        self.assertEqual([c[2] for c in cmds], ["track", "calibrate"])
        self.assertEqual(len(build_calibrate_commands("r.mov", "g", "c", track="t.csv", python="py")), 1)

    def test_output_dir_uses_cura_build_name(self):
        out = default_output_dir("/x/Movie on 10-8-26 at 6.38 PM.mov", "/b/circle/3D/model.gcode")
        self.assertTrue(out.name.endswith("_circle"))
        self.assertNotIn(" ", out.name)

    def test_cached_track_matches_mac_recording_name(self):
        (self.tmp / "v0521.csv").write_text("x")
        (self.tmp / "index.json").write_text(json.dumps({"Movie on 10-8-26 at 5.21 PM.mov": "v0521.csv"}))
        found = cached_track("/Users/x/Movie on 10-8-26 at 5.21\u202fPM.mov", self.tmp)
        self.assertEqual(found, self.tmp / "v0521.csv")
        self.assertIsNone(cached_track("other.mov", self.tmp))

    def test_remember_track_copies_once(self):
        src = self.tmp / "track.csv"
        src.write_text("a")
        target = remember_track("new video.mov", src, self.tmp / "tracks")
        self.assertEqual(target.read_text(), "a")
        self.assertEqual(cached_track("new video.mov", self.tmp / "tracks"), target)


if __name__ == "__main__":
    unittest.main()
