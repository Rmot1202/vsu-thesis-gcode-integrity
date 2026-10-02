"""Modal G-code path reader. Reference evidence never fills observation gaps."""
import math
import re
from .model import Point, Trajectory

WORDS=re.compile(r'([A-Z])\s*([-+]?(?:\d*\.\d+|\d+\.?\d*))')


def load_reference(path, scope='print', arc_step=1.):
    from pathlib import Path
    xyz=[None,None,None];offset=[0.,0.,0.];unit=1.;abs_xyz=True;abs_e=True
    tool='0';extruders={'0':0.};layer=None;layer_source='unknown';kind='unknown'
    points=[];notes=['Reference positions are commanded coordinates, not physical measurements.']
    def gap(line,reason):points.append(Point(str(line),reason=reason,provenance=['reference']))
    for line,raw in enumerate(Path(path).read_text().splitlines(),1):
        match=re.search(r';\s*LAYER:\s*(-?\d+)',raw,re.I)
        if match:layer=match[1];layer_source='reference_layer_comment';gap(line,'layer_change')
        match=re.search(r';\s*TYPE:\s*(.*)',raw,re.I)
        if match:
            label=match[1].upper();kind='perimeter' if 'WALL' in label else ('infill' if 'FILL' in label or 'SKIN' in label else 'print')
        code=re.sub(r'\([^)]*\)','',raw.split(';',1)[0]).strip().upper()
        words=WORDS.findall(code)
        if not words:continue
        if words[0][0]=='N':words=words[1:]
        if not words:continue
        letter,value=words[0];command=letter+str(int(float(value)))
        values={k:float(v) for k,v in words[1:]}
        if letter=='G' and float(value)!=int(float(value)):
            raise ValueError(f'Unsupported fractional G command at line {line}: {code}')
        if command=='G20':unit=25.4;continue
        if command=='G21':unit=1.;continue
        if command=='G90':abs_xyz=True;continue
        if command=='G91':abs_xyz=False;continue
        if command=='M82':abs_e=True;continue
        if command=='M83':abs_e=False;continue
        if command in ('G18','G19','G90.1') or (letter=='G' and float(value) not in (int(float(value)),)):
            raise ValueError(f'Unsupported reference coordinate mode at line {line}: {code}')
        if letter=='T':tool=str(int(float(value)));extruders.setdefault(tool,0.);gap(line,'tool_change');continue
        if command=='G92':
            for i,axis in enumerate('XYZ'):
                if axis in values:
                    logical=values[axis]*unit
                    if xyz[i] is None:xyz[i]=logical
                    offset[i]=xyz[i]-logical
            if 'E' in values:extruders[tool]=values['E']*unit
            gap(line,'coordinate_reset');continue
        if command in ('G28','G29','G53','G54','G55','G56','G57','G58','G59'):
            xyz=[None,None,None];offset=[0.,0.,0.];gap(line,'unmodeled_machine_coordinates');notes.append(f'{command} at line {line}: position invalidated');continue
        if command not in ('G0','G1','G2','G3'):
            if letter=='G' and command not in ('G17','G4'):
                gap(line,'unsupported_command');notes.append(f'Ignored non-path command {command} at line {line}')
            continue
        old=xyz.copy();new=xyz.copy();old_e=extruders[tool]
        for i,axis in enumerate('XYZ'):
            if axis in values:new[i]=values[axis]*unit+offset[i] if abs_xyz else (None if old[i] is None else old[i]+values[axis]*unit)
        new_e=old_e if 'E' not in values else (values['E']*unit if abs_e else old_e+values['E']*unit)
        extrusion=new_e-old_e;extruders[tool]=new_e;xyz=new
        is_arc=command in ('G2','G3')
        has_xy='X' in values or 'Y' in values or is_arc
        state=(kind if kind!='unknown' else 'print') if extrusion>1e-8 and command!='G0' else ('travel' if 'E' in values or command=='G0' else 'unknown')
        include=has_xy and (scope=='all' or state in ('print','perimeter','infill'))
        if not include:
            if has_xy or 'Z' in values or extrusion<0:gap(line,'excluded_nonprint_or_z_move')
            continue
        if any(v is None for v in old[:2]+new[:2]):gap(line,'unknown_start_position');continue
        samples=[old,new]
        if is_arc:
            if 'R' in values or not ('I' in values or 'J' in values):
                raise ValueError(f'Only G17 incremental I/J arcs supported (line {line}); linearize R arcs first')
            center=[old[0]+values.get('I',0)*unit,old[1]+values.get('J',0)*unit]
            radius=math.dist(old[:2],center)
            if radius<=1e-8 or abs(math.dist(new[:2],center)-radius)>max(.01,radius*.001):raise ValueError(f'Invalid arc at {line}')
            start=math.atan2(old[1]-center[1],old[0]-center[0]);end=math.atan2(new[1]-center[1],new[0]-center[0])
            sweep=(end-start)%(2*math.pi) if command=='G3' else -((start-end)%(2*math.pi))
            if abs(sweep)<1e-10:sweep=2*math.pi if command=='G3' else -2*math.pi
            count=max(2,math.ceil(abs(sweep)*radius/arc_step))
            if count>100000:raise ValueError('Arc sample limit exceeded; increase --arc-step-mm')
            samples=[]
            for i in range(count+1):
                f=i/count;angle=start+f*sweep
                z=None if old[2] is None or new[2] is None else old[2]+f*(new[2]-old[2])
                samples.append([center[0]+radius*math.cos(angle),center[1]+radius*math.sin(angle),z])
            notes.append(f'Arc line {line} tessellated to {count} segments')
        for index,point in enumerate(samples):
            points.append(Point(frame=f'{line}:{index}',xy=tuple(point[:2]),raw_xy=tuple(point[:2]),layer=layer,z=point[2],state=state,
                                provenance=['reference_command']+(['arc_tessellation'] if is_arc else []),layer_source=layer_source,
                                z_source='reference_command' if point[2] is not None else 'unknown',state_source='reference_extrusion_or_command'))
    return Trajectory(points,'bed_mm',[str(Path(path).resolve())],notes)
