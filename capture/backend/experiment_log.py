"""Permanent experiment records shared by both camera positions.

Startup cleanup touches only explicitly marked test runs. A protected record
is never a cleanup candidate, even after interruption or failed processing.
"""
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNS_ROOT = PROJECT_ROOT / 'outputs' / 'runs'
MARKER = '.experiment.json'
TEST_MARKER = '.test_run.json'
VIEWS = ('side_view','up_view','up_auto_view')


def timestamp():
    return datetime.now().astimezone().isoformat()


def write_json(path,data):
    path=Path(path)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(data,indent=2,default=str,allow_nan=False)+'\n')
    temporary.replace(path)


def startup_cleanup(root):
    """Delete declared test folders only; never infer permanence from age/name."""
    root=Path(root).expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
    removed=[]
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir() or (folder/MARKER).exists():
            continue
        marker=folder/TEST_MARKER
        if not marker.is_file() or marker.is_symlink():continue
        try:data=json.loads(marker.read_text())
        except (ValueError,OSError):continue
        if data.get('kind')=='print_capture_test_run' and data.get('delete_on_startup') is True:
            shutil.rmtree(folder);removed.append(folder.name)
    if removed:
        with (root/'startup_cleanup.jsonl').open('a') as f:
            f.write(json.dumps({'time':timestamp(),'deleted_test_runs':removed})+'\n')
    return removed


def add_capture_arguments(parser):
    parser.add_argument('--runs-dir','--save-dir',dest='runs_dir',type=Path,default=RUNS_ROOT,
                        help='Permanent experiment root; view/date/time folder created for each recording')
    parser.add_argument('--reference',type=Path,help='Original G-code; otherwise ask after recording')
    parser.add_argument('--no-reference-prompt',action='store_true',help='Leave comparison pending without terminal input')


class Experiment:
    def __init__(self,folder):
        self.folder=Path(folder).resolve()
        marker=self.folder/MARKER
        if not marker.is_file() or marker.is_symlink():raise ValueError('Not a permanent experiment folder')
        protection=json.loads(marker.read_text())
        if protection.get('permanent') is not True:raise ValueError('Run is not protected')
        self.metadata=json.loads((self.folder/'metadata.json').read_text())

    @classmethod
    def create(cls,root,view,settings):
        if view not in VIEWS:raise ValueError(f'Unknown view: {view}')
        root=Path(root).expanduser().resolve();root.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')
        folder=root/f'{view}_{stamp}';folder.mkdir(exist_ok=False)
        write_json(folder/MARKER,{'version':1,'permanent':True,'view':view,'created_at':timestamp()})
        write_json(folder/'metadata.json',{'view':view,'run_timestamp':stamp,'started_at':timestamp(),
                   'capture_status':'recording','processing_status':'pending','comparison_status':'pending',
                   'settings':settings,'permanent':True})
        # Pointer makes each folder understandable even if it is moved off this machine.
        (folder/'RUN_INFO.txt').write_text(
            f'Permanent experiment: {view}\nCapture files and analysis share this folder.\n'
            'Project documentation is centralized in capture/docs/.\n'
            'Do not execute path-only G-code on the printer.\n')
        print(f'Permanent run: {folder}',flush=True)
        return cls(folder)

    def update(self,**values):
        self.metadata.update(values);write_json(self.folder/'metadata.json',self.metadata)

    def copy_input(self,source,name):
        source=Path(source).expanduser().resolve();target=self.folder/name
        if not source.is_file():raise ValueError(f'Input does not exist: {source}')
        if source==target.resolve():return target
        if target.exists():
            history=self.folder/'input_history';history.mkdir(exist_ok=True)
            shutil.copy2(target,history/(datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+name))
        shutil.copy2(source,target)
        return target


def reference_prompt():
    if not sys.stdin.isatty():
        print('No interactive terminal; reference comparison left pending. Use experiment.py process later.')
        return None
    while True:
        try:answer=input('Original/reference G-code path (Enter = add later): ').strip().strip('"').strip("'")
        except (EOFError,KeyboardInterrupt):print();return None
        if not answer:return None
        path=Path(answer).expanduser()
        if path.is_file():return path
        print('File not found. Enter a valid path or press Enter to skip.')


def process_experiment(experiment,reference=None,prompt=False,**options):
    from backend.reconstruction.pipeline import parser,run
    session=experiment if isinstance(experiment,Experiment) else Experiment(experiment)
    if session.metadata.get('capture_status') == 'recording':
        raise ValueError('Stop recording before processing this run')
    if prompt and reference is None and not (session.folder/'original_reference.gcode').exists():
        reference=reference_prompt()
    try:
        if reference:session.copy_input(reference,'original_reference.gcode')
        reference=session.folder/'original_reference.gcode'
        argv=['--trajectory',str(session.folder/'trajectory.csv'),'--output-dir',str(session.folder)]
        if reference.exists():argv += ['--reference',str(reference)]
        for field,name in [('states','states.csv'),('calibration','calibration_input.json'),('alignment','alignment.json')]:
            value=options.pop(field,None)
            if value:session.copy_input(value,name)
            if (session.folder/name).exists():argv += ['--'+field,str(session.folder/name)]
        args=parser().parse_args(argv)
        for key,value in options.items():
            if value is not None:
                if not hasattr(args,key):raise ValueError(f'Unknown processing option: {key}')
                setattr(args,key,value)
        args.view=session.metadata['view'];args.run_timestamp=session.metadata['run_timestamp']
        session.update(processing_status='running')
        summary=run(args,experiment=True)
        comparable=bool(summary['comparison'].get('available'))
        status='pending' if not reference.exists() else ('available' if comparable else 'limited_coordinate_evidence')
        session.update(processing_status='complete',comparison_status=status,last_processed_at=timestamp(),
                       coordinate_space=summary['coordinate_space'],bed_xy_commands_available=summary['bed_xy_commands_available'])
        print(f'Analysis saved in {session.folder}; comparison: {status}',flush=True)
        return summary
    except Exception as error:
        session.update(processing_status='failed',comparison_status='processing_failed',processing_error=str(error))
        print(f'Run preserved; processing failed: {error}',file=sys.stderr)
        raise


def finish_capture(session,args,frames,status='finished',**extra):
    if session is None:return
    session.update(capture_status=status,ended_at=timestamp(),frames_recorded=frames,**extra)
    if frames==0:
        session.update(processing_status='no_frames',comparison_status='pending');return
    try:
        process_experiment(session,args.reference,
                           prompt=not args.no_reference_prompt and status=='finished')
    except Exception:
        # Captured data must remain usable even if parsing/reference processing fails.
        pass


def publish_analysis(stage,output):
    """Replace only owned analysis artifacts, preserving all capture and input files."""
    from backend.reconstruction.pipeline import MANIFEST
    output=Path(output);session=Experiment(output)
    if session.metadata.get('capture_status')=='recording':raise ValueError('Stop recording before processing this run')
    manifest=output/MANIFEST
    old=json.loads(manifest.read_text())['files'] if manifest.exists() else []
    new=json.loads((stage/MANIFEST).read_text())['files']
    protected={'metadata.json',MARKER,'trajectory.csv','original_reference.gcode','RUN_INFO.txt','raw_video.mp4','annotated_video.mp4'}
    for name in set(old+new):
        if Path(name).name!=name or name in protected:raise ValueError('Unsafe analysis manifest entry')
        if (output/name).is_symlink() or (output/name).is_dir():raise ValueError('Invalid analysis output path')
        if name in new and (output/name).exists() and name not in old:raise ValueError(f'Unmanaged file collision: {name}')
    if manifest.exists():
        history=output/'analysis_history'/datetime.now().strftime('%Y%m%d_%H%M%S_%f');history.mkdir(parents=True)
        for name in old+[MANIFEST]:
            source=output/name
            if source.exists():shutil.move(str(source),str(history/name))
    for source in stage.iterdir():shutil.move(str(source),str(output/source.name))
