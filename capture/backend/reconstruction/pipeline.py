"""CLI orchestration and reproducible, non-destructive output publication."""
import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from .ingest import load_trajectory
from .alignment import align
from .processing import organize_layers, clean, segment, smooth
from .reference import load_reference
from .export import write_gcode,write_csv,visualize,compare,write_griffin_analysis

MANIFEST='.reconstruction_manifest.json'


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def publish(stage,output):
    output=Path(output)
    if output.exists():
        if not output.is_dir():raise ValueError('Output must be a directory')
        entries={p.name for p in output.iterdir()}
        if entries:
            if MANIFEST not in entries:raise ValueError('Refusing to modify unmanaged nonempty output directory; select a new one')
            manifest=json.loads((output/MANIFEST).read_text())
            owned=set(manifest['files'])|{MANIFEST}
            if entries-owned:raise ValueError('Output contains unrecognized files; select a new directory to preserve them')
            if any(Path(n).name!=n or (output/n).is_dir() for n in entries):raise ValueError('Invalid output manifest')
            archive=output.parent/(output.name+'_archive')/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            archive.parent.mkdir(parents=True,exist_ok=True)
            output.rename(archive)
    elif not output.parent.exists():output.parent.mkdir(parents=True)
    if output.exists():output.rmdir() # empty only
    shutil.move(str(stage),str(output))


def parser():
    p=argparse.ArgumentParser(description='General conservative camera-path reconstruction. Analysis only.')
    p.add_argument('--trajectory',type=Path,required=True)
    p.add_argument('--reference',type=Path)
    p.add_argument('--calibration',type=Path)
    p.add_argument('--states',type=Path,help='Frame-indexed layer/state/Z evidence CSV')
    p.add_argument('--alignment',type=Path,help='JSON of explicit rigid bed-space correspondences')
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--coordinates',choices=['auto','bed','pixels','side'],default='auto')
    p.add_argument('--print-start-seconds',type=float,help='Video time after startup/calibration')
    p.add_argument('--print-end-seconds',type=float,help='Video time at the end of the actual print')
    p.add_argument('--reference-scope',choices=['print','all'],default='print')
    p.add_argument('--max-gap-seconds',type=float,default=2.)
    p.add_argument('--max-jump',type=float,help='Spatial discontinuity threshold in selected coordinate units')
    p.add_argument('--pause-seconds',type=float,default=1.)
    p.add_argument('--pause-radius',type=float,default=.05,help='Selected coordinate units')
    p.add_argument('--min-confidence',type=float,default=0.)
    p.add_argument('--smooth-alpha',type=float,help='Optional EMA; omitted by default')
    p.add_argument('--interpolate-max-points',type=int,default=0,help='Opt-in short-gap interpolation; default none')
    p.add_argument('--layers-from-z',action='store_true',help='Hypothesize layers from classified deposition Z only')
    p.add_argument('--layer-z-tolerance',type=float,default=.05)
    p.add_argument('--arc-step-mm',type=float,default=1.)
    p.add_argument('--per-layer-plots',action='store_true')
    return p


def run(args, experiment=False):
    import math
    positive=['max_gap_seconds','pause_seconds','layer_z_tolerance','arc_step_mm']
    for key in positive:
        if not math.isfinite(getattr(args,key)) or getattr(args,key)<=0:raise ValueError(f'{key} must be finite and positive')
    if not 0<=args.min_confidence<=1 or args.interpolate_max_points<0 or not math.isfinite(args.pause_radius) or args.pause_radius<0:raise ValueError('Invalid confidence/interpolation/pause settings')
    if args.smooth_alpha is not None and not 0<args.smooth_alpha<=1:raise ValueError('EMA alpha must be in (0,1]')
    if args.max_jump is not None and (not math.isfinite(args.max_jump) or args.max_jump<=0):raise ValueError('max-jump must be finite and positive')
    inputs=[p.resolve() for p in [args.trajectory,args.reference,args.calibration,args.states,args.alignment] if p]
    output=args.output_dir.resolve()
    if not experiment and any(output==p or output in p.parents for p in inputs):raise ValueError('Inputs must be outside the output directory')
    for p in inputs:
        if not p.is_file():raise ValueError(f'Input not found: {p}')
    observation=load_trajectory(args.trajectory,args.coordinates,args.calibration,args.states)
    start=args.print_start_seconds; end=args.print_end_seconds
    if (start is None) != (end is None):
        raise ValueError('Provide both --print-start-seconds and --print-end-seconds')
    if start is not None:
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
            raise ValueError('Print window must satisfy 0 <= start < end with finite times')
        if any(p.time is None for p in observation.points):
            raise ValueError('Print-window filtering requires timestamps on every observation')
        for point in observation.points:
            if not start <= point.time <= end:
                point.xy=None
                point.reason='outside_print_window'
                point.provenance.append('startup_or_postprint_excluded')
            else:
                point.provenance.append('user_supplied_print_window')
        if not any(p.xy is not None for p in observation.points):
            raise ValueError('No valid tracked points in the supplied print window')
        observation.notes.append('Startup and post-print motion excluded by user-supplied video times; no automatic extrusion classification.')
    else:
        observation.notes.append('Print window not supplied: camera path may contain startup/calibration motion.')
    reference=load_reference(args.reference,args.reference_scope,args.arc_step_mm) if args.reference else None
    registration=align(observation,args.alignment,bool(reference))
    # Capture imported/aligned records before denoising for auditing.
    from copy import deepcopy
    raw=deepcopy(observation.points)
    organize_layers(observation.points,args.layers_from_z,args.layer_z_tolerance)
    counts=clean(observation.points,args.max_jump,args.min_confidence,args.interpolate_max_points,args.max_gap_seconds)
    segments=segment(observation.points,args.max_gap_seconds,args.max_jump,args.pause_seconds,args.pause_radius)
    reconstructed=smooth(segments,args.smooth_alpha)
    ref_segments=segment(reference.points,float('inf'),None,float('inf')) if reference else []
    layers=list(dict.fromkeys(s.layer for s in reconstructed if s.layer is not None))
    ref_layers=list(dict.fromkeys(s.layer for s in ref_segments if s.layer is not None))
    from collections import Counter
    rejection_counts=dict(Counter(p.reason for p in observation.points if p.xy is None))
    summary={'rejected_point_reasons':rejection_counts,'analysis_only':True,'partial_reconstruction':True,'reference_available':bool(reference),
             'coordinate_space':observation.space,'bed_xy_commands_available':observation.space=='bed_mm' and bool(reconstructed),
             'input_hashes':{str(p):digest(p) for p in inputs},'settings':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
             'alignment':registration,'reference_layer_order':ref_layers,'observed_layer_order':layers,
             'layer_structure':'unknown' if not layers else ('single supplied/inferred layer' if len(layers)==1 else 'multiple supplied/inferred layers'),
             'reference_layers_without_observed_label':[x for x in ref_layers if x not in layers],
             'layers_note':'Missing layer labels do not prove missing physical layers. Reference layers are never copied into observations.',
             'input_rows':len(raw),'valid_input_points':sum(p.xy is not None for p in raw),'segments':len(reconstructed),'cleanup':counts,
             'unknowns':['extrusion E','feedrate F','temperature','fan','acceleration','tool state','unobserved motion',
                         'layer index where absent','Z where absent','print/travel classification where absent'],
             'provenance_tags':sorted({t for s in reconstructed for p in s.points for t in p.provenance}),
             'notes':observation.notes+(reference.notes if reference else []),
             'comparison':compare(ref_segments,reconstructed,observation.space)}
    stage=Path(tempfile.mkdtemp(prefix='reconstruction_'))
    try:
        write_gcode(stage/'reference_path_only.gcode',ref_segments,'bed_mm',bool(reference),'REFERENCE_COMMANDS' if reference else 'REFERENCE_UNAVAILABLE')
        write_gcode(stage/'reconstructed_path_only.gcode',reconstructed,observation.space,bool(reference),'CAMERA_RECONSTRUCTION')
        if len(layers)>1:write_gcode(stage/'reconstructed_layered_path_only.gcode',reconstructed,observation.space,bool(reference),'CAMERA_LAYERED_RECONSTRUCTION')
        if args.reference:
            write_griffin_analysis(stage/'reconstructed_griffin_analysis.gcode',
                                  args.reference, reconstructed, observation.space, start, end)
        write_csv(stage/'observations_raw.csv',raw)
        write_csv(stage/'observations_cleaned.csv',observation.points)
        write_csv(stage/'reconstructed_points.csv',[p for s in reconstructed for p in s.points])
        write_csv(stage/'reference_points.csv',reference.points if reference else [])
        visualize(stage,ref_segments,reconstructed,observation.space,args.per_layer_plots)
        (stage/'reconstruction_summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
        text=['GENERAL RECONSTRUCTION - ANALYSIS ONLY / NOT FOR PRINTING',
              f'Reference available: {bool(reference)}',f'Coordinate space: {observation.space}',
              f'Bed XY commands exported: {summary["bed_xy_commands_available"]}',f'Layer structure: {summary["layer_structure"]}',
              f'Observed layer order: {layers}; reference layer order: {ref_layers}',
              'Alignment: '+json.dumps(registration),
              'Sources: '+', '.join(map(str,inputs)),
              'Provenance: '+', '.join(summary['provenance_tags']),
              'Cleanup: '+json.dumps(counts),
              'Rejected point reasons: '+json.dumps(rejection_counts),
              'Unknown: '+', '.join(summary['unknowns']),
              'Confidence: supplied scores are retained, not inferred or calibrated by this pipeline.',
              'Estimated calibration, interpolation, smoothing and hypothetical layers are flagged.',
              'No missing segments, closed loops, layer heights or printer settings are invented.',
              'Uncalibrated side pixels cannot produce bed XY. Plots remain in their native coordinate frame.',
              'G0 segment anchors are not observed safe travel; gaps are not joined in plots.',
              'No automatic time registration or assurance that reference and camera show the same print.',
              'Settings: '+json.dumps(summary['settings']),*summary['notes']]
        (stage/'reconstruction_summary.txt').write_text('\n'.join(text)+'\n')
        context = {'view':getattr(args,'view','unspecified'), 'run_timestamp':getattr(args,'run_timestamp','unspecified')}
        for gcode in stage.glob('*.gcode'):
            content=gcode.read_text()
            gcode.write_text('; VIEW:'+context['view']+'\n; RUN_TIMESTAMP:'+context['run_timestamp']+'\n'+content)
        comparison = summary['comparison']
        (stage/'comparison_summary.txt').write_text(
            'ANALYSIS ONLY - reference comparison\n'+json.dumps(comparison,indent=2)+'\n')
        files=sorted(p.name for p in stage.iterdir())
        (stage/MANIFEST).write_text(json.dumps({'version':1,'files':files},indent=2)+'\n')
        if experiment:
            from backend.experiment_log import publish_analysis
            publish_analysis(stage,output)
        else:
            publish(stage,output)
    finally:
        if stage.exists():shutil.rmtree(stage)
    return summary


def main(argv=None):
    p=parser();args=p.parse_args(argv)
    try:summary=run(args)
    except (ValueError,OSError,KeyError) as error:p.exit(2,f'Reconstruction failed: {error}\n')
    print(f'Saved analysis outputs to {args.output_dir.resolve()}')
    print(f'Coordinates: {summary["coordinate_space"]}; segments: {summary["segments"]}; layer structure: {summary["layer_structure"]}')
    if not summary['bed_xy_commands_available']:print('No bed XY commands emitted: see summary for missing coordinate evidence.')
