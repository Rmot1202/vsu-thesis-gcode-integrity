"""Explicit correspondence registration; no auto-fitting a known shape to pixels."""
import json
from pathlib import Path
import numpy as np


def align(trajectory, path, reference_available):
    if not path:return None
    if trajectory.space!='bed_mm':
        raise ValueError('Registration requires existing bed_mm observations; cannot promote side/pixel data to XY')
    config=json.loads(Path(path).read_text())
    source=np.asarray(config['source_points'],float);target=np.asarray(config['target_points'],float)
    if source.ndim!=2 or source.shape[1]!=2 or source.shape!=target.shape or len(source)<2 or not np.isfinite([source,target]).all():
        raise ValueError('Alignment requires >=2 corresponding finite 2D source/target points')
    provenance=config.get('provenance','user_supplied_correspondences')
    if provenance=='reference_aligned' and not reference_available:
        raise ValueError('reference_aligned registration requires --reference')
    a=source-source.mean(axis=0);b=target-target.mean(axis=0)
    if np.linalg.norm(a)<1e-8 or np.linalg.norm(b)<1e-8:raise ValueError('Degenerate alignment landmarks')
    u,_,vt=np.linalg.svd(a.T@b);rotation=vt.T@u.T
    if np.linalg.det(rotation)<0:
        vt[-1]*=-1;rotation=vt.T@u.T
    # Rigid registration preserves measured scale; no shape-dependent warping.
    translation=target.mean(axis=0)-rotation@source.mean(axis=0)
    residual=source@rotation.T+translation-target
    for point in trajectory.points:
        if point.xy is not None:
            point.xy=tuple(rotation@point.xy+translation)
            point.provenance.append(provenance)
    return {'kind':'rigid_2d','provenance':provenance,'rotation':rotation.tolist(),'translation':translation.tolist(),
            'landmark_rmse_mm':float(np.sqrt(np.mean(np.sum(residual**2,axis=1)))), 'source':str(Path(path).resolve())}
