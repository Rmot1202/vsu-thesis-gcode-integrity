#!/usr/bin/env python3
"""One entry point: capture -> permanent record -> reference -> reconstruction."""
import argparse
import importlib.util
from pathlib import Path
from experiment_log import Experiment,RUNS_ROOT,VIEWS,startup_cleanup,process_experiment

ROOT=Path(__file__).resolve().parent


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    capture=sub.add_parser('capture',help='Start a camera tracker; extra options pass to that tracker')
    capture.add_argument('--view',choices=VIEWS,required=True)
    process=sub.add_parser('process',help='Reconstruct an existing permanent record or add reference later')
    process.add_argument('--run',type=Path,required=True)
    imported=sub.add_parser('import',help='Copy an existing trajectory into a new permanent experiment')
    imported.add_argument('--trajectory',type=Path,required=True)
    imported.add_argument('--view',choices=VIEWS,required=True)
    imported.add_argument('--runs-dir',type=Path,default=RUNS_ROOT)
    for command in (process,imported):
        command.add_argument('--reference',type=Path)
        command.add_argument('--no-reference-prompt',action='store_true')
        command.add_argument('--states',type=Path)
        command.add_argument('--calibration',type=Path)
        command.add_argument('--alignment',type=Path)
        command.add_argument('--coordinates',choices=['auto','bed','pixels','side'])
        command.add_argument('--smooth-alpha',type=float)
        command.add_argument('--max-jump',type=float)
        command.add_argument('--per-layer-plots',action='store_true',default=None)
    cleanup=sub.add_parser('cleanup-tests',help='Delete explicitly marked test runs; permanent records are excluded')
    cleanup.add_argument('--runs-dir',type=Path,default=RUNS_ROOT)
    args,extra=parser.parse_known_args(argv)
    if args.command!='capture' and extra:parser.error('Unrecognized arguments: '+' '.join(extra))
    if args.command=='capture':
        relative={'side_view':'side_camera/side_nozzle_tracker.py','up_view':'upward_camera/nozzle_grid_tracker.py','up_auto_view':'upward_camera/auto_nozzle_tracker.py'}[args.view]
        spec=importlib.util.spec_from_file_location('capture_tracker',ROOT/relative)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.main(extra)
    elif args.command=='cleanup-tests':
        print('Deleted test folders:',startup_cleanup(args.runs_dir))
    else:
        if args.command=='import':
            if not args.trajectory.is_file():parser.error('Trajectory file does not exist')
            startup_cleanup(args.runs_dir)
            session=Experiment.create(args.runs_dir,args.view,{'imported_from':str(args.trajectory.resolve())})
            session.copy_input(args.trajectory,'trajectory.csv')
            session.update(capture_status='imported',raw_video_available=False)
        else:session=Experiment(args.run)
        options={k:getattr(args,k) for k in ('states','calibration','alignment','coordinates','smooth_alpha','max_jump','per_layer_plots')}
        process_experiment(session,args.reference,prompt=not args.no_reference_prompt,**options)


if __name__=='__main__':main()
