# Side-camera tracking

The GUI defaults to side view. The side tracker uses the trained
`printer_head` detector and a physical nozzle-tip selection to track the
printer head. To get printer-space XY from a side camera, it also needs a
validated calibration mapping from side-view observations to known bed
positions. Pixels or side-plane measurements alone are not bed XY.

## Prepare and calibrate

1. Train or select a reviewed `printer_head` detector; see [training](training.md).
2. In the GUI, choose **Prepare calibration G-code copy…**, select the original,
   then save the new copy. The equivalent CLI command is:

   ```bash
   python application/side_xy_calibration_gcode.py \
     --input inputs/references/model.gcode \
     --output outputs/post_analysis/model_side_xy_calibration.gcode
   ```

   The tool inserts labeled XY moves and five-second dwells immediately before
   the single-layer Cura marker used by the supplied circle example. It refuses
   to overwrite the input or an existing destination, and does not change the
   source file. It is not a general G-code editor; other files with different
   startup markers are rejected. Inspect the generated copy and verify that
   positions are reachable, the bed is clear, and the camera can see the head.
   Supervise the first run. This routine continues into the original print.
3. Use a prerecorded video of the complete seven-stop calibration sequence or
   connect to the live camera while running the sequence. From the GUI, choose
   that video file with **Choose video…** (or set the camera index), then click
   **Auto-calibrate…** beside the calibration field. Choose a new JSON output
   location and confirm. The equivalent CLI command is:

   ```bash
   python application/collect_side_xy_calibration.py \
     --input /path/to/side_xy_calibration_video.mp4 \
     --model outputs/training/legacy_dataset/runs/printer-head-3/weights/best.pt \
     --output outputs/training/side_xy_calibration.json
   ```

   Select the trained model and input first. In the camera window, click the
   printer-head box, SHIFT-click the physical nozzle tip, then press **S** as
   point A begins. For prerecorded video, start the clip just before point A;
   the collector uses the video's frame timestamps when deciding whether a
   stop lasted long enough, rather than the file-processing speed. It then
   assigns stable holds in programmed order, writes the samples CSV beside the
   JSON, fits the mapping, and reports held-out RMSE. Do not arm before the
   sequence or after point A has settled; the camera has no printer telemetry.
   If a tracking gap or missed hold occurs, quit and repeat rather than trusting
   shifted point labels.
4. Inspect the saved CSV and held-out RMSE before using the fitted mapping.
   The calibration JSON must declare `coordinate_model=calibrated_side_xy`
   and `coordinate_space=bed_mm`.
5. Configure the
   resulting calibration JSON and model in the GUI.

The sequence has 35 seconds of dwell time, plus travel. It keeps the original
startup Z clearance and then lets the original print continue. Its coordinates,
clearance, firmware compatibility, and calibration accuracy have not been
physically verified on the printer. The calibration samples are taken at the
startup Z height; do not assume the mapping remains accurate at the print Z.
The source comments label points, but comments are not visible to the camera.
Collection relies on the operator arming at point A, detecting head motion and
stable holds, and assigning the next hold by sequence order; it does not read
printer telemetry or infer calibration completion from the G-code end marker.
No automatic pass/fail RMSE threshold is currently enforced.

The mapping estimates the depth axis from apparent head width and the other
bed axis from perspective-corrected nozzle position. It rejects estimates
outside the calibrated bounds. Keep the camera, zoom, head orientation, and
calibration setup fixed; parallax and out-of-plane motion can invalidate the
mapping. Do not treat side-plane homography coordinates as printer XY.

## CLI use

From `capture/`:

```bash
python application/experiment.py capture --view side_view --input 0
```

Or run the camera module directly:

```bash
python cameras/side/side_nozzle_tracker.py --input 0
```

Select the head when prompted and SHIFT-click the physical nozzle tip. The
tracker records the camera evidence and calibrated XY provenance for
reconstruction. For video input, replace camera index `0` with a video path.

## Limits

Side-view XY is accepted by reconstruction only when the calibration metadata
and each observation explicitly identify the calibrated side-to-bed coordinate
source. Without valid calibration, the tracker may still provide pixels or
side-plane coordinates, but the reconstruction does not promote them to bed
millimeters. Side-view extrusion classification is not implemented. Any
exported G-code is analysis-only, not printer-ready. See [outputs](OUTPUTS.md).
