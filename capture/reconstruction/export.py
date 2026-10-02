"""Analysis-only G-code, provenance CSV, PNG plots and conservative comparisons."""
import csv
import json
from dataclasses import asdict
from pathlib import Path
import cv2
import numpy as np


def write_gcode(path,segments,space,reference_assisted,role):
    with open(path,'w') as f:
        headers=['PARTIAL_RECONSTRUCTION:YES','GENERAL_PIPELINE:YES','ANALYSIS_ONLY:YES','NOT_FOR_PRINTING',
                 f'REFERENCE_ASSISTED:{"YES" if reference_assisted else "NO"}','MULTILAYER_SUPPORTED:YES',
                 'EXTRUSION_UNKNOWN:YES','FEEDRATE_UNKNOWN:YES','TEMPERATURE_UNKNOWN:YES',f'ROLE:{role}',
                 f'COORDINATE_SPACE:{space}','COORDINATE_CONVENTION:absolute millimeters; no machine setup emitted',
                 'G0 at segment start is an analysis anchor, NOT a verified safe connecting move',
                 'G1 describes observed geometry, NOT proof of extrusion']
        f.write(''.join('; '+x+'\n' for x in headers))
        if space!='bed_mm':
            f.write('; NO_XY_COMMANDS: observations are not calibrated printer-bed XY\n')
            for layer in dict.fromkeys(s.layer for s in segments):
                f.write(f';LAYER:{layer if layer is not None else "UNKNOWN"}\n; See reconstructed_points.csv for native-coordinate observations\n')
            return
        if not segments:
            f.write('; NO_SUPPORTED_PATH_AVAILABLE\n');return
        last_layer=object()
        for index,segment in enumerate(segments):
            if segment.layer!=last_layer:
                f.write(f'\n;LAYER:{segment.layer if segment.layer is not None else "UNKNOWN"}\n')
                last_layer=segment.layer
            f.write(f';SEGMENT:{index} state={segment.state} break={segment.reason}\n')
            for i,p in enumerate(segment.points):
                f.write('; '+json.dumps({'frame':p.frame,'time':p.time,'provenance':p.provenance,'confidence':p.confidence,
                                         'layer_source':p.layer_source,'z_source':p.z_source,'state_source':p.state_source},separators=(',',':'))+'\n')
                if p.z is None:
                    f.write('; Z_UNSPECIFIED: do not interpret modal Z as measured\n')
                command='G0' if i==0 or p.state=='travel' else 'G1'
                z=f' Z{p.z:.6f}' if p.z is not None else ''
                f.write(f'{command} X{p.xy[0]:.6f} Y{p.xy[1]:.6f}{z}\n')


def write_csv(path,points):
    keys=['frame','time','x','y','raw_x','raw_y','layer','z','state','confidence','provenance','layer_source','z_source','state_source','reason']
    with open(path,'w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys);writer.writeheader()
        for p in points:
            row=asdict(p);xy=row.pop('xy');raw=row.pop('raw_xy')
            row.update(x=xy[0] if xy else '',y=xy[1] if xy else '',raw_x=raw[0] if raw else '',raw_y=raw[1] if raw else '')
            row['provenance']='|'.join(row['provenance']);writer.writerow(row)


def plot_panel(series,title,space,width=1000,height=700):
    canvas=np.full((height,width,3),250,np.uint8)
    def label(message,y):cv2.putText(canvas,message,(20,y),cv2.FONT_HERSHEY_SIMPLEX,.52,(35,35,35),1,cv2.LINE_AA)
    label(title,28);label(f'Coordinates: {space} | breaks are not connected | colors identify series/layers',52)
    pts=[p.xy for segments,_,_ in series for s in segments for p in s.points if p.xy is not None]
    if not pts:label('No supported points available',100);return canvas
    array=np.array(pts,float);low=array.min(axis=0);high=array.max(axis=0)
    span=np.maximum(high-low,1e-9);scale=min((width-100)/span[0],(height-170)/span[1])
    def pixel(xy):
        x=50+(xy[0]-low[0])*scale
        y=90+((xy[1]-low[1]) if space=='image_px' else (high[1]-xy[1]))*scale
        return int(round(x)),int(round(y))
    palette=[(50,130,220),(100,180,40),(170,60,160),(0,150,180),(180,100,30)]
    layers=list(dict.fromkeys(s.layer for segs,_,_ in series for s in segs))
    for segments,name,color in series:
        for s in segments:
            c=color if color is not None else palette[layers.index(s.layer)%len(palette)]
            points=np.int32([pixel(p.xy) for p in s.points])
            if len(points)>1:cv2.polylines(canvas,[points],False,c,2,cv2.LINE_AA)
            elif len(points):cv2.circle(canvas,tuple(points[0]),3,c,-1)
    label(f'Horizontal range: {low[0]:.3f} .. {high[0]:.3f}; vertical: {low[1]:.3f} .. {high[1]:.3f}',height-48)
    label(' | '.join(name for _,name,_ in series),height-22)
    return canvas


def save_png(path, image):
    if not cv2.imwrite(str(path),image):raise OSError(f'Failed to save {path}')


def visualize(directory,reference,reconstructed,space,per_layer=False):
    save_png(directory/'reference_path.png',plot_panel([(reference,'Reference',None)],'Reference path (source G-code)','bed_mm'))
    save_png(directory/'reconstructed_path.png',plot_panel([(reconstructed,'Observation',None)],'Reconstructed observation; layers color-coded',space))
    if space=='bed_mm' and reference:
        overlay=plot_panel([(reference,'Reference: blue',(220,110,40)),(reconstructed,'Observation: orange',(20,140,240))],
                           'Reference / observation: common bed frame; correspondence not assumed',space)
    else:
        left=plot_panel([(reference,'Reference',None)],'NO METRIC OVERLAY: reference','bed_mm',800,650)
        right=plot_panel([(reconstructed,'Observation',None)],'Separate observation coordinates',space,800,650)
        overlay=np.hstack([left,right])
    save_png(directory/'overlay_comparison.png',overlay)
    if per_layer:
        layers=list(dict.fromkeys(s.layer for s in reference+reconstructed))
        for layer in layers:
            name='unknown' if layer is None else layer
            observed=[s for s in reconstructed if s.layer==layer];ref=[s for s in reference if s.layer==layer]
            save_png(directory/f'layer_{name}.png',plot_panel([(observed,'Observation',None)],f'Observed layer {name}',space))
            if space=='bed_mm':
                save_png(directory/f'layer_overlay_{name}.png',plot_panel([(ref,'Reference: blue',(220,110,40)),(observed,'Observation: orange',(20,140,240))],f'Layer {name}: supplied layer labels',space))


def compare(reference,reconstructed,space):
    if space!='bed_mm' or not reference or not reconstructed:
        return {'available':False,'reason':'Requires reference and bed_mm observations; no pixel/side overlay metric'}
    def nearest(a,b):
        # Bounded sample count and chunked matrix; diagnostic only, not time registration.
        a=a[::max(1,int(np.ceil(len(a)/2000)))];b=b[::max(1,int(np.ceil(len(b)/2000)))];values=[]
        for i in range(0,len(a),100):
            d=((a[i:i+100,None,:]-b[None,:,:])**2).sum(axis=2)
            values.extend(np.sqrt(d.min(axis=1)))
        return float(np.mean(values))
    def samples(segs):
        # Include points along sparse reference line segments, preserving separate islands.
        values=[]
        for s in segs:
            for p in s.points:values.append(p.xy)
            for a,b in zip(s.points,s.points[1:]):
                n=min(50,max(1,int(np.linalg.norm(np.array(b.xy)-a.xy))))
                values.extend(tuple(np.array(a.xy)*(1-t)+np.array(b.xy)*t) for t in np.linspace(0,1,n+1)[1:-1])
        return np.array(values)
    result={'available':True,'metric':'bidirectional sampled nearest-neighbour distance, mm; not timing/sequence error',
            'warning':'No assertion that recordings are the same print. Registration fitting may reduce residuals.', 'layers':{}}
    for layer in dict.fromkeys(s.layer for s in reconstructed):
        a=[s for s in reference if s.layer==layer];b=[s for s in reconstructed if s.layer==layer]
        if not a:continue
        x,y=samples(a),samples(b)
        result['layers'][str(layer)]={'observation_to_reference_mean_mm':nearest(y,x),'reference_to_observation_mean_mm':nearest(x,y)}
    if not result['layers']:result['note']='No matched layer evidence; layerwise metrics omitted.'
    return result
