"""Extract XY deposition paths from absolute-XYZ Cura/Griffin G-code.

Uses extrusion changes only to classify print vs travel. Export contains NO
extrusion, temperature, Z, feed rate or printer startup commands. Inspection
artifact, not a complete machine-ready program. Optional side CSV produces
an image-space SVG without pretending its coordinates are printer X/Y.
"""
import argparse
import csv
import json
import math
import re
from pathlib import Path


def extract(source):
    xy=[None,None];e=0.;absolute_e=True;absolute_xyz=True;segments=[];segment=None
    for number,raw in enumerate(source.splitlines(),1):
        code=raw.split(';',1)[0].strip().upper()
        words=re.findall(r'([A-Z])\s*([-+]?(?:\d*\.\d+|\d+\.?\d*))',code)
        if not words:continue
        command=words[0][0]+str(int(float(words[0][1])))
        values={key:float(value) for key,value in words[1:]}
        if command=='G90':absolute_xyz=True
        elif command=='G91':absolute_xyz=False
        elif command=='M82':absolute_e=True
        elif command=='M83':absolute_e=False
        elif command=='G92':
            e=values.get('E',e)
            for i,axis in enumerate('XY'):xy[i]=values.get(axis,xy[i])
            segment=None
        elif command in ('G2','G3'):
            raise ValueError(f'Arc command at line {number} is unsupported; linearize first.')
        elif command in ('G0','G1'):
            if not absolute_xyz:raise ValueError('This extractor requires absolute XYZ coordinates.')
            next_xy=[values.get(axis,xy[i]) for i,axis in enumerate('XY')]
            next_e=values.get('E',e) if absolute_e else e+values.get('E',0.)
            has_xy='X' in values or 'Y' in values
            printing=command=='G1' and has_xy and next_e>e+1e-8
            if printing:
                if any(v is None for v in xy+next_xy):raise ValueError('Unknown start coordinates')
                if segment is None:
                    segment=[{'x':xy[0],'y':xy[1],'source_line':number,'point_role':'segment_start'}]
                    segments.append(segment)
                segment.append({'x':next_xy[0],'y':next_xy[1],'source_line':number,'point_role':'print_endpoint'})
            elif has_xy or next_e<e or 'Z' in values:segment=None
            xy=next_xy;e=next_e
    if not segments:raise ValueError('No XY deposition path found')
    return segments


def svg(path,segments,title,y_up):
    points=[p for segment in segments for p in segment]
    if not points:return
    xs=[p[0] for p in points];ys=[p[1] for p in points]
    x0,x1=min(xs),max(xs);y0,y1=min(ys),max(ys)
    scale=min(700/max(x1-x0,1),450/max(y1-y0,1))
    lines=[]
    for segment in segments:
        coords=' '.join(f'{50+(x-x0)*scale:.2f},{75+((y1-y) if y_up else (y-y0))*scale:.2f}' for x,y in segment)
        lines.append(f'<polyline points="{coords}" fill="none" stroke="#1565c0" stroke-width="2"/>')
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><rect width="100%" height="100%" fill="white"/><text x="20" y="30" font-family="sans-serif" font-size="18">'+title+'</text>'+''.join(lines)+'</svg>')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('gcode',type=Path);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--side-csv',type=Path)
    args=p.parse_args();segments=extract(args.gcode.read_text())
    args.output_dir.mkdir(parents=True,exist_ok=True)
    destination=args.output_dir/'square_reference_xy.gcode'
    if destination.exists():raise FileExistsError(f'Refusing to overwrite {destination}')
    lines=['; XY PRINT-PATH REFERENCE - extracted from slicer G-code, NOT camera reconstructed',
           '; Inspection only. No Z clearance, extrusion, temperature, feed rate or homing.',
           '; Segment-start G0 positions are schematic; do not execute without adapting setup.',
           'G21','G90']
    for index,segment in enumerate(segments):
        lines.append(f'; Print segment {index+1}')
        for i,point in enumerate(segment):
            lines.append(f'{"G0" if i==0 else "G1"} X{point["x"]:.3f} Y{point["y"]:.3f}')
    destination.write_text('\n'.join(lines)+'\n')
    with open(args.output_dir/'square_reference_xy.csv','w',newline='') as f:
        writer=csv.writer(f);writer.writerow(['segment','point','x_mm','y_mm','source_line','point_role','provenance'])
        for j,segment in enumerate(segments):
            for i,point in enumerate(segment):writer.writerow([j,i,point['x'],point['y'],point['source_line'],point['point_role'],'slicer_gcode'])
    paths=[[(p['x'],p['y']) for p in s] for s in segments]
    svg(args.output_dir/'square_reference_xy.svg',paths,'Slicer reference: printer XY (mm), Y upward',True)
    points=[p for s in paths for p in s]
    summary={'provenance':'slicer_gcode_not_camera','source':str(args.gcode.resolve()),
             'segments':len(paths),'xy_bounds_mm':{'min_x':min(p[0] for p in points),'max_x':max(p[0] for p in points),'min_y':min(p[1] for p in points),'max_y':max(p[1] for p in points)},
             'print_path_length_mm':sum(math.dist(a,b) for s in paths for a,b in zip(s,s[1:]))}
    if args.side_csv:
        trails=[];current=[];valid=total=0;times=[]
        with open(args.side_csv,newline='') as f:
            for row in csv.DictReader(f):
                total+=1
                try:
                    x,y=float(row['image_u_px']),float(row['image_v_px']);t=float(row['elapsed_seconds'])
                    ok=row['tracking_valid']=='1' and all(math.isfinite(v) for v in (x,y,t))
                except (ValueError,KeyError):ok=False
                if ok:current.append((x,y));valid+=1;times.append(t)
                elif current:trails.append(current);current=[]
        if current:trails.append(current)
        svg(args.output_dir/'side_observed_pixels.svg',trails,'Camera observation: image pixels, Y downward (NOT bed XY)',False)
        summary['side_observation']={'source':str(args.side_csv.resolve()),'rows':total,'tracked_rows':valid,'elapsed_span_seconds':max(times)-min(times) if times else None,'units':'image_pixels','same_print_as_reference':'not_verified','note':'No time/path registration or automatic perimeter isolation performed.'}
    (args.output_dir/'path_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
