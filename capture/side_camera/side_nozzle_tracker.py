#!/usr/bin/env python3
"""Side-camera nozzle tracking. Pixel output or calibrated side-plane coordinates.

Not bed XY, not 3D, and not nozzle-to-bed gap measurement. A metric reference
must be stationary and coplanar with the observed nozzle motion. Depth motion
causes parallax. Uses the existing project's CSRT, LED and movement helpers.
"""
import argparse
import csv
import json
import time
from collections import deque
from datetime import datetime
from pathlib import Path

# Support direct execution from any working directory; shared code is in capture/.
import sys
CAPTURE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CAPTURE_ROOT))

import cv2
import numpy as np
from experiment_log import Experiment, add_capture_arguments, startup_cleanup, finish_capture
from tracking_common import create_tracker, HeadLightTracker, MovementTrigger

WINDOW = 'Side view | S ROI | SHIFT-click tip | C calibrate | SPACE pause | V record | A arm | Q quit'
TIP_FRACTION = (.5, 1.0)
TRAIL_LENGTH = 2000
RED_STRIP_HSV_LOW_1 = (0, 70, 60)
RED_STRIP_HSV_HIGH_1 = (10, 255, 255)
RED_STRIP_HSV_LOW_2 = (170, 70, 60)
RED_STRIP_HSV_HIGH_2 = (179, 255, 255)
RED_STRIP_MIN_AREA = 40
RED_STRIP_MAX_AREA = 6000
RED_STRIP_MIN_ASPECT_RATIO = 1.5
RED_STRIP_SEARCH_RADIUS_PX = 45
RED_STRIP_MAX_WITHOUT_CSRT_FRAMES = 60


class RedStripTracker:
    """Track one wide red head marker using its fixed image-space tip offset."""
    def __init__(self):
        self.offset = None
        self.area = None
        self.last_tip = None
        self.lost_frames = 0
        self.visible = None

    def detect(self, frame):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        low_hues = cv2.inRange(hsv, np.array(RED_STRIP_HSV_LOW_1), np.array(RED_STRIP_HSV_HIGH_1))
        high_hues = cv2.inRange(hsv, np.array(RED_STRIP_HSV_LOW_2), np.array(RED_STRIP_HSV_HIGH_2))
        mask = cv2.bitwise_or(low_hues, high_hues)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        for contour in contours:
            area = cv2.contourArea(contour)
            if not RED_STRIP_MIN_AREA <= area <= RED_STRIP_MAX_AREA:
                continue
            _, (width, height), _ = cv2.minAreaRect(contour)
            short_side = min(width, height)
            if short_side <= 0 or max(width, height) / short_side < RED_STRIP_MIN_ASPECT_RATIO:
                continue
            moments = cv2.moments(contour)
            if moments['m00']:
                center = np.array([moments['m10']/moments['m00'], moments['m01']/moments['m00']])
                blobs.append({'center': center, 'area': area, 'contour': contour})
        return blobs

    def update(self, frame, box, csrt_tip):
        self.visible = None
        self.lost_frames = 0 if csrt_tip is not None else self.lost_frames + 1
        if self.lost_frames > RED_STRIP_MAX_WITHOUT_CSRT_FRAMES:
            self.last_tip = None
            return None
        blobs = self.detect(frame)
        if self.offset is None:
            if box is None or csrt_tip is None:
                return None
            x, y, width, height = box
            inside = [blob for blob in blobs if x <= blob['center'][0] <= x + width
                      and y <= blob['center'][1] <= y + height]
            if len(inside) != 1:
                return None
            marker = inside[0]
            self.offset = csrt_tip - marker['center']
            self.area = marker['area']

        reference = csrt_tip if csrt_tip is not None else self.last_tip
        if reference is None:
            return None
        predicted = reference - self.offset
        matches = [blob for blob in blobs
                   if .5 <= blob['area'] / self.area <= 2
                   and np.linalg.norm(blob['center'] - predicted) <= RED_STRIP_SEARCH_RADIUS_PX]
        if len(matches) != 1:
            self.last_tip = csrt_tip.copy() if csrt_tip is not None else self.last_tip
            return None
        marker = matches[0]
        self.visible = marker
        self.last_tip = marker['center'] + self.offset
        return self.last_tip.copy()


def side_homography(points, width_mm, height_mm):
    """Click TL, TR, BR, BL; side coordinates start bottom-left, vertical up."""
    points = np.asarray(points, np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        return None
    if not cv2.isContourConvex(points.reshape(-1, 1, 2)) or cv2.contourArea(points) < 100:
        return None
    matrix = cv2.getPerspectiveTransform(points, np.float32([[0,height_mm],[width_mm,height_mm],[width_mm,0],[0,0]]))
    if not np.isfinite(matrix).all() or np.linalg.cond(matrix)>1e8:
        return None
    denominator = np.c_[points, np.ones(4)] @ matrix[2]
    if denominator.min()*denominator.max()<=0:
        return None
    return matrix


def map_point(point, matrix):
    if point is None or matrix is None:
        return None
    denominator = matrix[2] @ np.array([*point, 1.])
    if abs(denominator)<1e-8:
        return None
    result=cv2.perspectiveTransform(np.float32(point).reshape(1,1,2),matrix)[0,0]
    return result if np.isfinite(result).all() else None


class Selection:
    def __init__(self):
        self.calibrating=False
        self.corners=[]
        self.tip=None

    def mouse(self,event,x,y,flags,param):
        if event!=cv2.EVENT_LBUTTONDOWN:return
        if self.calibrating:
            if len(self.corners)<4:self.corners.append((x,y))
        elif flags & cv2.EVENT_FLAG_SHIFTKEY:
            self.tip=np.array([x,y],float)


def annotate(frame,box,tip,metric,trail,selection,recording,armed,matrix,red_strip=None,source="NONE"):
    display=frame.copy()
    if red_strip is not None and red_strip.visible is not None:
        cv2.drawContours(display,[red_strip.visible['contour']],-1,(0,0,255),2)
        cv2.putText(display,'RED STRIP',tuple(red_strip.visible['center'].astype(int)),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,0,255),2)
    previous=None
    for point in trail:
        if point is not None:
            p=tuple(np.rint(point).astype(int))
            if previous is not None:cv2.line(display,previous,p,(0,210,255),2)
            previous=p
        else:previous=None
    if box is not None:
        x,y,w,h=map(int,box);cv2.rectangle(display,(x,y),(x+w,y+h),(255,180,0),2)
    if tip is not None:cv2.drawMarker(display,tuple(np.rint(tip).astype(int)),(255,0,255),cv2.MARKER_CROSS,16,2)
    for label,p in zip(('TL','TR','BR','BL'),selection.corners):
        cv2.circle(display,p,5,(0,255,0),-1)
        cv2.putText(display,label,p,cv2.FONT_HERSHEY_SIMPLEX,.6,(0,255,0),2)
    if len(selection.corners)==4:cv2.polylines(display,[np.int32(selection.corners)],True,(0,255,0),2)
    lines=[('RECORDING' if recording else ('AUTO ARMED - waiting for movement' if armed else 'AUTO OFF - A to arm'))+f' | SIDE VIEW | {source}',
           'Pixel: unavailable' if tip is None else f'Pixel u={tip[0]:.1f}, v={tip[1]:.1f}',
           ('Side-plane mm: unavailable' if matrix is not None else 'PIXEL MODE - no metric calibration') if metric is None else f'Side horizontal={metric[0]:.2f} mm, vertical={metric[1]:.2f} mm',
           'S ROI | SHIFT-click tip | C calibrate | SPACE pause | V record | A arm | Q quit']
    if selection.calibrating:lines.append('PAUSED: click reference TL, TR, BR, BL | R cancels')
    for i,line in enumerate(lines):
        cv2.putText(display,line,(12,25+25*i),cv2.FONT_HERSHEY_SIMPLEX,.48,(0,0,0),3)
        cv2.putText(display,line,(12,25+25*i),cv2.FONT_HERSHEY_SIMPLEX,.48,(255,255,255),1)
    return display


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='0',help='Webcam index or video path')
    add_capture_arguments(parser)
    parser.add_argument('--reference-width-mm',type=float)
    parser.add_argument('--reference-height-mm',type=float)
    parser.add_argument('--no-led',action='store_true')
    parser.add_argument('--no-red-strip',action='store_true',help='Disable red head-strip assistance')
    args=parser.parse_args(argv)
    startup_cleanup(args.runs_dir)
    dims=(args.reference_width_mm,args.reference_height_mm)
    if any(v is not None for v in dims) and not all(v is not None and np.isfinite(v) and v>0 for v in dims):
        parser.error('Specify both positive reference dimensions, or neither.')
    cap=cv2.VideoCapture(int(args.input) if args.input.isdecimal() else args.input)
    selection=Selection();tracker=None;box=None;matrix=None;tip=None
    fraction=np.array(TIP_FRACTION);lights=HeadLightTracker();movement=MovementTrigger()
    red_strip=RedStripTracker();source="NONE"
    trail=deque(maxlen=TRAIL_LENGTH);paused=False;armed=True
    writer=handle=raw_writer=None;frame_index=0;recorded=0
    session=None
    capture_status='interrupted'

    def close_recording(status='finished'):
        nonlocal writer,handle,raw_writer,session
        if writer is not None:writer.release()
        if raw_writer is not None:raw_writer.release()
        if handle is not None:handle.close()
        writer=handle=raw_writer=None
        finished=session;session=None
        finish_capture(finished,args,recorded,status,final_tip_fraction=fraction.tolist())
    try:
        if not cap.isOpened():raise RuntimeError(f'Cannot open {args.input}')
        fps=cap.get(cv2.CAP_PROP_FPS)
        if not np.isfinite(fps) or fps<=0:fps=30
        cv2.namedWindow(WINDOW,cv2.WINDOW_AUTOSIZE);cv2.setMouseCallback(WINDOW,selection.mouse)
        ok,frame=cap.read()
        if not ok:raise RuntimeError('No readable frame')
        while True:
            if not paused:
                box=None;tip=None
                if tracker is not None:
                    valid,current=tracker.update(frame)
                    if valid:
                        box=tuple(current)
                        tip=np.array(box[:2])+np.array(box[2:])*fraction
                csrt_tip=tip
                source='CSRT' if tip is not None else 'NONE'
                if not args.no_led:
                    tip=lights.update(frame,box,csrt_tip)
                    if lights.color:source='LED_'+lights.color
                red_strip_tip=None if args.no_red_strip else red_strip.update(frame,box,csrt_tip)
                if red_strip_tip is not None:
                    tip=red_strip_tip;source='RED_STRIP'
                if tip is None:source='NONE' 
                trail.append(None if tip is None else tip.copy())
            if selection.tip is not None:
                if box is not None:
                    fraction=(selection.tip-np.array(box[:2]))/np.array(box[2:])
                    tip=selection.tip.copy();lights=HeadLightTracker();red_strip=RedStripTracker();movement.reset();trail.append(None)
                else:print('Select a head ROI with S before setting its tip.')
                selection.tip=None
            if selection.calibrating and len(selection.corners)==4:
                matrix=side_homography(selection.corners,*dims)
                selection.calibrating=False
                if matrix is None:selection.corners=[];print('Invalid reference geometry; press C to retry.')
                else:print('Side-plane calibration ready. SPACE resumes. Coordinates are not bed XY.')
            metric=map_point(tip,matrix)
            request_start=not paused and writer is None and armed and movement.update(tip)
            display=annotate(frame,box,tip,metric,trail,selection,writer is not None,armed,matrix,red_strip,source)
            cv2.imshow(WINDOW,display)
            key=cv2.waitKey(30 if paused else max(1,round(1000/fps)))&255
            quit_requested=key==ord('q') or cv2.getWindowProperty(WINDOW,cv2.WND_PROP_VISIBLE)<1
            if key==ord('v'):
                if writer is not None:
                    close_recording();armed=False;movement.reset()
                else:request_start=True
            if request_start and not quit_requested:
                recorded=0
                session=Experiment.create(args.runs_dir,'side_view',{'input':args.input,'reference_dimensions_mm':dims,
                    'recording_trigger':'manual' if key==ord('v') else 'nozzle_movement',
                    'coordinate_system':'Side plane only; not printer XY or Z gap','tip_fraction':fraction.tolist()})
                folder=session.folder
                h,w=frame.shape[:2];size=(w+w%2,h+h%2)
                writer=cv2.VideoWriter(str(folder/'annotated_video.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
                if not writer.isOpened():raise RuntimeError('Cannot create recording')
                raw_writer=cv2.VideoWriter(str(folder/'raw_video.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
                if not raw_writer.isOpened():raise RuntimeError('Cannot create raw recording')
                handle=open(folder/'trajectory.csv','w',newline='');csv_writer=csv.writer(handle)
                csv_writer.writerow(['frame_index','elapsed_seconds','image_u_px','image_v_px','side_horizontal_mm','side_vertical_mm','tracking_valid','calibration_valid','image_to_side_homography','tracking_source','view_mode'])
                started=time.monotonic();recorded=0;print(f'Recording: {folder}')
            if writer is not None and not paused:
                display=annotate(frame,box,tip,metric,trail,selection,True,armed,matrix,red_strip,source)
                raw_writer.write(cv2.copyMakeBorder(frame,0,size[1]-frame.shape[0],0,size[0]-frame.shape[1],cv2.BORDER_CONSTANT))
                writer.write(cv2.copyMakeBorder(display,0,size[1]-frame.shape[0],0,size[0]-frame.shape[1],cv2.BORDER_CONSTANT))
                csv_writer.writerow([frame_index,time.monotonic()-started,*(tip if tip is not None else ('','')),*(metric if metric is not None else ('','')),int(tip is not None),int(matrix is not None),json.dumps(matrix.tolist()) if matrix is not None else '',source,'side_view']);recorded+=1
            if quit_requested:break
            if key==ord('s'):
                roi=cv2.selectROI(WINDOW,frame,fromCenter=False,showCrosshair=True)
                cv2.setMouseCallback(WINDOW,selection.mouse)
                if roi[2]>0 and roi[3]>0:
                    tracker=create_tracker('CSRT');tracker.init(frame,roi);box=roi
                    tip=np.array(roi[:2])+np.array(roi[2:])*fraction
                    lights=HeadLightTracker();red_strip=RedStripTracker();movement.reset();trail.append(None)
                paused=True
            elif key==ord('c'):
                if dims[0] is None:print('Restart with --reference-width-mm and --reference-height-mm for side-plane calibration.')
                else:paused=True;selection.calibrating=True;selection.corners=[];matrix=None
            elif key==ord('r'):
                selection.calibrating=False;selection.corners=[];matrix=None
            elif key==ord('a'):armed=True;movement.reset()
            elif key==ord(' ') and not selection.calibrating:paused=not paused
            if not paused:
                ok,frame=cap.read();frame_index+=1
                if not ok:break
        capture_status='finished'
    finally:
        cap.release();cv2.destroyAllWindows()
        close_recording(capture_status)


if __name__=='__main__':main()
