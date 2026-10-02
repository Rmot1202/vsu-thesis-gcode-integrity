"""CSV adapters, explicit state evidence, and conservative coordinate mapping."""
import csv
import json
import math
from pathlib import Path
import numpy as np
from .model import Point, Trajectory


def number(value):
    if value is None or str(value).strip() == '':
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def pair(row, keys):
    values = tuple(number(row.get(k)) for k in keys)
    return values if all(v is not None for v in values) else None


def matrix_point(xy, matrix):
    h = np.asarray(matrix, float)
    if h.shape != (3, 3) or not np.isfinite(h).all() or np.linalg.cond(h) > 1e10:
        raise ValueError('Homography must be a finite, nonsingular 3x3 matrix')
    point = h @ np.array([*xy, 1.])
    if abs(point[2]) < 1e-8:
        return None
    result = point[:2] / point[2]
    return tuple(result) if np.isfinite(result).all() else None


def load_states(path):
    if not path:
        return {}
    with open(path, newline='') as handle:
        result = {}
        for row in csv.DictReader(handle):
            key = row.get('frame_index', row.get('frame'))
            if key is None or key in result:
                raise ValueError('State CSV requires unique frame_index/frame identifiers')
            result[key] = row
        return result


def load_trajectory(path, coordinates='auto', calibration=None, states=None):
    metadata = json.loads(Path(calibration).read_text()) if calibration else {}
    evidence = load_states(states)
    sources = [str(Path(path).resolve())]
    sources.extend(str(Path(p).resolve()) for p in (calibration, states) if p)
    with open(path, newline='') as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        if not fields:
            raise ValueError('Empty trajectory CSV')
        bed_keys = next((keys for keys in [('nozzle_x_bed_mm','nozzle_y_bed_mm'), ('x_mm','y_mm')]
                         if set(keys) <= fields), None)
        side_keys = ('side_horizontal_mm','side_vertical_mm')
        pixel_keys = next((keys for keys in [('image_u_px','image_v_px'), ('nozzle_u_px','nozzle_v_px'), ('image_x','image_y')]
                           if set(keys) <= fields), None)
        if coordinates == 'auto':
            coordinates = 'bed' if bed_keys or metadata.get('coordinate_space') == 'bed_mm' else 'pixels'
        if coordinates == 'bed' and not bed_keys and not (pixel_keys and metadata.get('coordinate_space') == 'bed_mm'):
            raise ValueError('Bed export requires millimeter columns or explicit image_to_bed calibration metadata')
        if coordinates == 'pixels' and pixel_keys is None:
            raise ValueError('No supported image-pixel columns; specify --coordinates bed for x_mm/y_mm')
        if coordinates == 'side' and not set(side_keys) <= fields:
            raise ValueError('No side-plane millimeter columns')
        static_h = metadata.get('image_to_bed_homography')
        if coordinates == 'bed' and bed_keys is None and static_h is None and 'image_to_bed_homography' not in fields:
            raise ValueError('Calibration lacks image_to_bed_homography')
        points=[]; seen=set(); unused=set(evidence)
        for index,row in enumerate(reader):
            frame = row.get('frame_index', row.get('frame',str(index)))
            if frame in seen:
                raise ValueError(f'Duplicate frame identifier {frame}; use one capture per run')
            seen.add(frame)
            state_row = evidence.get(frame,{})
            unused.discard(frame)
            merged = {**row, **{k:v for k,v in state_row.items() if v != ''}}
            def val(name): return merged.get(name)
            provenance=['camera_observation']
            if row.get('tracking_source'):
                provenance.append('tracker:' + row['tracking_source'])
            valid = all(row.get(flag,'1') not in ('0','False','false') for flag in ('tracking_valid','tracking_ok','tracker_valid'))
            # Side-plane coordinates can NEVER silently become printer XY.
            if coordinates == 'bed' and 'side' in row.get('view_mode','').lower():
                raise ValueError('Side-camera observations cannot be treated as bed XY by this adapter')
            xy = None
            if coordinates == 'bed':
                valid &= all(row.get(flag,'1') not in ('0','False','false') for flag in ('calibration_valid','homography_valid','inside_bed'))
                if bed_keys:
                    xy = pair(row,bed_keys)
                    provenance.append('calibrated_bed_coordinates')
                else:
                    raw = pair(row,pixel_keys)
                    h = json.loads(row['image_to_bed_homography']) if row.get('image_to_bed_homography') else static_h
                    xy = matrix_point(raw,h) if raw is not None and h is not None else None
                    provenance.append('homography_mapped')
                if 'ESTIMATED' in row.get('calibration_state','').upper():
                    provenance.append('estimated_calibration')
                elif not row.get('calibration_state'):
                    provenance.append('calibration_freshness_unknown')
            elif coordinates == 'side':
                valid &= row.get('calibration_valid','1') != '0'
                xy=pair(row,side_keys)
                provenance.append('side_plane_only')
            else:
                xy=pair(row,pixel_keys)
            layer = val('layer_index') or val('layer') or None
            if layer is not None:
                try: layer=str(int(layer))
                except ValueError: raise ValueError(f'Layer must be an integer at frame {frame}')
            motion=val('motion_state') or 'unknown'
            if motion not in ('unknown','print','travel','pause','perimeter','infill'):
                raise ValueError(f'Unsupported motion_state {motion!r}')
            z=number(val('z_mm'))
            confidence=number(val('confidence'))
            if confidence is not None and not 0<=confidence<=1: raise ValueError('confidence must be 0..1')
            p=Point(frame=frame,time=number(row.get('elapsed_seconds',row.get('time_seconds'))),
                    xy=xy if valid else None,raw_xy=xy,layer=layer,z=z,state=motion,confidence=confidence,
                    provenance=provenance,layer_source=val('layer_source') or ('supplied_layer_index' if layer is not None else 'unknown'),
                    z_source=val('z_source') or ('supplied_z_mm' if z is not None else 'unknown'),
                    state_source=val('state_source') or ('supplied_classification' if motion!='unknown' else 'unknown'),
                    reason='' if valid and xy is not None else 'invalid_or_missing_observation')
            points.append(p)
    notes=[]
    if unused: notes.append(f'{len(unused)} state records did not match a trajectory frame')
    if not points:raise ValueError('Trajectory CSV contains no rows')
    return Trajectory(points, {'bed':'bed_mm','side':'side_mm','pixels':'image_px'}[coordinates], sources, notes)
