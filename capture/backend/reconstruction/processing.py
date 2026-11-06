"""Conservative layer organization, gaps/outliers, segmentation and optional EMA."""
from dataclasses import replace
import math
import numpy as np
from .model import Segment


def organize_layers(points, infer_from_z=False, tolerance=.05):
    if not infer_from_z:return
    index=-1;previous_z=None
    for point in points:
        if point.layer is not None:
            raise ValueError('Do not combine explicit layer indices and --layers-from-z')
        # Travel Z hops do not constitute layer evidence. Restrict to classified deposition.
        if point.xy is None or point.z is None or point.state not in ('print','perimeter','infill'):
            continue
        if previous_z is None or abs(point.z-previous_z)>tolerance:
            index+=1;previous_z=point.z
        point.layer=str(index)
        point.layer_source='hypothesized_from_deposition_z'
        point.provenance.append('hypothesized_layer')


def clean(points, max_jump, min_confidence, interpolate, max_gap):
    counts={'outliers':0,'low_confidence':0,'interpolated':0}
    # Isolated spike removal needs agreement from both neighbours, not just a large move.
    for i,point in enumerate(points):
        if point.confidence is not None and point.confidence<min_confidence:
            point.xy=None;point.reason='low_confidence';counts['low_confidence']+=1
    original=[p.xy for p in points]
    if max_jump is not None:
        for i in range(1,len(points)-1):
            a,b,c=original[i-1:i+2]
            if any(p is None for p in (a,b,c)):continue
            if not (points[i-1].layer==points[i].layer==points[i+1].layer):continue
            if (math.dist(a,b)>max_jump and math.dist(b,c)>max_jump and math.dist(a,c)<=max_jump):
                points[i].xy=None;points[i].reason='isolated_spike_rejected';counts['outliers']+=1
    if interpolate:
        i=0
        while i<len(points):
            if points[i].xy is not None:i+=1;continue
            start=i
            while i<len(points) and points[i].xy is None:i+=1
            if start==0 or i==len(points) or i-start>interpolate:continue
            a,b=points[start-1],points[i]
            if a.time is None or b.time is None or not 0<b.time-a.time<=max_gap:continue
            if a.layer!=b.layer or a.state!=b.state:continue
            if max_jump is not None and math.dist(a.xy,b.xy)>max_jump:continue
            for point in points[start:i]:
                if point.layer!=a.layer or point.state!=a.state:break
                if point.time is None or not a.time<point.time<b.time:break
            else:
                for point in points[start:i]:
                    fraction=(point.time-a.time)/(b.time-a.time)
                    point.xy=tuple(np.array(a.xy)*(1-fraction)+np.array(b.xy)*fraction)
                    point.provenance.append('interpolated_linear')
                    point.reason='interpolated_short_gap'
                    counts['interpolated']+=1
    return counts


def segment(points, max_gap, max_jump=None, pause_seconds=1., pause_radius=.05):
    segments=[];current=[];reason='start';stationary_since=None;anchor=None
    def flush():
        nonlocal current
        if current:segments.append(Segment(current,current[0].layer,current[0].state,reason));current=[]
    previous=None
    for point in points:
        if point.xy is None:
            flush();previous=None;reason=point.reason or 'missing';anchor=None;stationary_since=None;continue
        boundary=None
        if previous is not None:
            if point.layer!=previous.layer:boundary='layer_change'
            elif point.state!=previous.state:boundary='state_change'
            elif point.time is not None and previous.time is not None:
                dt=point.time-previous.time
                if dt<=0:boundary='nonmonotonic_timestamp'
                elif dt>max_gap:boundary='time_gap'
            if max_jump is not None and math.dist(point.xy,previous.xy)>max_jump:boundary='spatial_jump'
        if boundary:
            flush();reason=boundary;anchor=None;stationary_since=None
        if anchor is None or math.dist(anchor,point.xy)>pause_radius:
            anchor=point.xy;stationary_since=point.time
        elif point.time is not None and stationary_since is not None and point.time-stationary_since>=pause_seconds:
            flush();reason='pause_boundary';anchor=point.xy;stationary_since=point.time
        current.append(point);previous=point
    flush()
    return segments


def smooth(segments, alpha):
    if alpha is None:return segments
    result=[]
    for s in segments:
        previous=None;points=[]
        for point in s.points:
            xy=np.array(point.xy)
            xy=xy if previous is None else alpha*xy+(1-alpha)*previous
            points.append(replace(point,xy=tuple(xy),provenance=point.provenance+['ema_smoothed']))
            previous=xy
        result.append(Segment(points,s.layer,s.state,s.reason))
    return result
