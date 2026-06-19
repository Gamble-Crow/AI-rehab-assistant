"""
camera.py — WorkoutTracker + MJPEG server ngầm
================================================
- Không còn cv2.imshow() / cửa sổ riêng
- MJPEG server chạy ngầm trên http://127.0.0.1:MJPEG_PORT/video
- app.html nhúng: <img src="http://127.0.0.1:8765/video">
- Giữ nguyên toàn bộ logic đếm rep, tính góc, MediaPipe, YOLO
- Giữ nguyên get_cam_data()
"""

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode
from ultralytics import YOLO
import time
import urllib.request
from urllib.parse import urlparse, parse_qs, unquote
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

MJPEG_PORT   = 8765
CAMERA_INDEX = 0
FRAME_WIDTH  = 1280
FRAME_HEIGHT = 720

# Tang sang phong toi bang gamma (chinh tay): <1 = sang hon, lift vung toi manh
CAM_GAMMA  = 0.55
_GAMMA_LUT = np.array([((i / 255.0) ** CAM_GAMMA) * 255 for i in range(256)], dtype=np.uint8)

DISPLAY_CONFIG = {
    "show_exercise_name": True,
    "show_rep_count":     True,
    "show_stage":         True,
    "show_angle":         True,
    "show_fps":           True,
    "show_skeleton":      True,
    "show_bounding_box":  True,
    "show_timer":         True,
    "show_confidence":    False,
}

DEFAULT_EXERCISE = "Gap/duoi khuyu tay"

EXERCISES = {
    "squat": {
        "name": "SQUAT", "joints": (23,25,27),
        "down_angle": 90,  "up_angle": 160,
        "ideal_rep_seconds": (2.5, 5.0),
    },
    "pushup": {
        "name": "PUSH-UP", "joints": (11,13,15),
        "down_angle": 70,  "up_angle": 160,
        "ideal_rep_seconds": (2.0, 4.0),
    },
    "lunge": {
        "name": "LUNGE", "joints": (23,25,27),
        "down_angle": 85,  "up_angle": 160,
        "ideal_rep_seconds": (2.5, 5.0),
    },
    "Gap/duoi khuyu tay": {
        "name": "Gap/duoi khuyu tay", "joints": (12,14,16),
        "down_angle": 35,  "up_angle": 165,
        "ideal_rep_seconds": (2.0, 4.0),
    },
    "truot goi": {
        "name": "Truot goi", "joints": (23,25,27),
        "down_angle": 60,  "up_angle": 165,
        "ideal_rep_seconds": (2.5, 5.0),
    },
    "nang chan thang": {
        "name": "Nang chan thang", "joints": (11,23,25),
        "down_angle": 145,   "up_angle": 165,
        "ideal_rep_seconds": (3.0, 6.0),
    },
}

POSE_CONNECTIONS = [
    (11,12),(11,13),(13,15),(12,14),(14,16),
    (11,23),(12,24),(23,24),
    (23,25),(25,27),(24,26),(26,28),
]

C = {
    "white":(255,255,255),"black":(0,0,0),"green":(0,210,90),
    "yellow":(0,220,230),"cyan":(220,200,0),"orange":(0,140,255),
    "red":(0,60,220),"bg":(30,30,30),"accent":(0,180,255),
    "skeleton":(50,200,50),"joint":(0,140,255),
}

# ── Cache model: nap 1 lan, dung lai moi buoi ──
_YOLO_MODEL = None
_LANDMARKER = None

def _get_yolo():
    global _YOLO_MODEL
    if _YOLO_MODEL is None:
        print("[INFO] Loading YOLO (once)...")
        _YOLO_MODEL = YOLO("yolov8n.pt")
    return _YOLO_MODEL

def _get_landmarker():
    global _LANDMARKER
    if _LANDMARKER is None:
        print("[INFO] Loading MediaPipe Pose (once)...")
        opts = PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=ensure_model()),
            running_mode=RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.6,
            min_pose_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        _LANDMARKER = PoseLandmarker.create_from_options(opts)
    return _LANDMARKER


# ══════════════════════════════════════════════════════════════════════════════
# MJPEG SERVER
# ══════════════════════════════════════════════════════════════════════════════

_frame_lock  = threading.Lock()
_latest_jpeg = b""

def _set_frame(frame: np.ndarray):
    global _latest_jpeg
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    if ok:
        with _frame_lock:
            _latest_jpeg = buf.tobytes()

class _MJPEGHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def do_GET(self):
        if self.path.startswith("/clip"):
            self._serve_clip(); return
        if self.path != "/video":
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type",
                         "multipart/x-mixed-replace; boundary=--jpgboundary")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            while True:
                with _frame_lock:
                    jpeg = _latest_jpeg
                if jpeg:
                    self.wfile.write(b"--jpgboundary\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                time.sleep(0.033)
        except (ConnectionError, OSError):
            pass

    def _serve_clip(self):
        # Phuc vu file video trong thu muc video/, ho tro Range (206) cho <video>
        rel  = unquote((parse_qs(urlparse(self.path).query).get("f") or [""])[0]).replace("\\", "/")
        base = os.path.dirname(os.path.abspath(__file__))
        path = os.path.normpath(os.path.join(base, rel))
        vdir = os.path.join(base, "video")
        if not path.startswith(vdir) or not os.path.isfile(path):
            self.send_response(404); self.end_headers(); return
        try:
            data = open(path, "rb").read()
        except OSError:
            self.send_response(404); self.end_headers(); return

        total = len(data)
        ctype = mimetypes.guess_type(path)[0] or "video/mp4"
        rng   = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, e = rng[6:].split("-")
                start = int(s) if s else 0
                end   = int(e) if e else total - 1
            except ValueError:
                start, end = 0, total - 1
            start = max(0, start); end = min(end, total - 1)
            body  = data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(len(body)))
        else:
            body = data
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(total))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (ConnectionError, OSError):
            pass

def start_mjpeg_server(port: int = MJPEG_PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), _MJPEGHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[CAM] MJPEG: http://127.0.0.1:{port}/video")
    return server


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def calc_angle(a, b, c):
    a=np.array(a); b=np.array(b); c=np.array(c)
    ba=a-b; bc=c-b
    cos=np.dot(ba,bc)/(np.linalg.norm(ba)*np.linalg.norm(bc)+1e-6)
    return float(np.degrees(np.arccos(np.clip(cos,-1.,1.))))

def rounded_rect(img,x,y,w,h,r,color,alpha=0.7):
    ov=img.copy()
    cv2.rectangle(ov,(x+r,y),(x+w-r,y+h),color,-1)
    cv2.rectangle(ov,(x,y+r),(x+w,y+h-r),color,-1)
    for cx,cy in [(x+r,y+r),(x+w-r,y+r),(x+r,y+h-r),(x+w-r,y+h-r)]:
        cv2.circle(ov,(cx,cy),r,color,-1)
    cv2.addWeighted(ov,alpha,img,1-alpha,0,img)

def txt(img,text,pos,scale,color,thick=2):
    cv2.putText(img,text,pos,cv2.FONT_HERSHEY_SIMPLEX,scale,C["black"],thick+2,cv2.LINE_AA)
    cv2.putText(img,text,pos,cv2.FONT_HERSHEY_SIMPLEX,scale,color,thick,cv2.LINE_AA)

def fmt_time(s):
    return f"{int(s)//60:02d}:{int(s)%60:02d}"

def ensure_model(path="pose_landmarker_full.task"):
    if os.path.exists(path): return path
    url=("https://storage.googleapis.com/mediapipe-models/"
         "pose_landmarker/pose_landmarker_full/float16/latest/"
         "pose_landmarker_full.task")
    print("[INFO] Downloading MediaPipe model (~30MB)...")
    urllib.request.urlretrieve(url, path)
    return path


# ══════════════════════════════════════════════════════════════════════════════
# WORKOUT TRACKER
# ══════════════════════════════════════════════════════════════════════════════

class WorkoutTracker:

    def __init__(self, config: dict):
        # config lay tu DB qua start_session:
        #   {"name","joints":(a,b,c),"down_angle","up_angle","ideal_rep_seconds":(lo,hi)}
        self.yolo       = _get_yolo()
        self.landmarker = _get_landmarker()

        self._cfg = config
        self._k   = "cur"               # 1 bai / buoi -> 1 key duy nhat
        keys      = [self._k]

        self.reps   = {k: 0    for k in keys}
        self.stages = {k: None for k in keys}
        self.angle  = 0.0

        self.t0=time.time(); self.paused=False
        self.pause_acc=0.0;  self.pause_start=0.0
        self.fps=0;          self.ftimes=[]

        self._rep_start_time   = {k: None  for k in keys}
        self._min_angle_in_rep = {k: 180.0 for k in keys}
        self._rep_speeds       = {k: []    for k in keys}
        self._rep_roms         = {k: []    for k in keys}

        self.STABLE_FRAMES_REQUIRED = 10
        self._stable_frames = {k: 0     for k in keys}
        self._ready         = {k: False for k in keys}

        self._stop  = False
        self.on_rep = None   # callback(rep_current, total)

        print("[INFO] Tracker ready:", config.get("name"))

    @property
    def ck(self): return self._k
    @property
    def ce(self): return self._cfg

    def stop(self):          self._stop = True
    def toggle_pause(self):
        if not self.paused:  self.paused=True;  self.pause_start=time.time()
        else:                self.paused=False; self.pause_acc+=time.time()-self.pause_start

    def elapsed(self):
        base=self.pause_start if self.paused else time.time()
        return base-self.t0-self.pause_acc

    def reset(self):
        k=self.ck; self.reps[k]=0; self.stages[k]=None
        self._rep_start_time[k]=None; self._min_angle_in_rep[k]=180.0
        self._rep_speeds[k].clear(); self._rep_roms[k].clear()
        self._stable_frames[k]=0; self._ready[k]=False

    def update_fps(self):
        now=time.time(); self.ftimes.append(now)
        self.ftimes=[t for t in self.ftimes if now-t<1.0]
        self.fps=len(self.ftimes)

    def detect_pose(self, frame, timestamp_ms, box=None):
        H,W=frame.shape[:2]
        if box:
            x1,y1,x2,y2=box; pad=30
            x1=max(0,x1-pad); y1=max(0,y1-pad)
            x2=min(W,x2+pad); y2=min(H,y2+pad)
            crop=frame[y1:y2,x1:x2]; off=(x1,y1)
        else:
            crop=frame; off=(0,0)
        h_c,w_c=crop.shape[:2]
        rgb=cv2.cvtColor(crop,cv2.COLOR_BGR2RGB)
        mp_img=mp.Image(image_format=mp.ImageFormat.SRGB,data=rgb)
        res=self.landmarker.detect_for_video(mp_img,timestamp_ms)
        if res.pose_landmarks and len(res.pose_landmarks)>0:
            return res.pose_landmarks[0],w_c,h_c,off
        return None,w_c,h_c,off

    def count_rep(self, angle: float) -> bool:
        ex=self.ce; k=self.ck; st=self.stages[k]
        if not self._ready[k]:
            if angle>ex["up_angle"]:
                self._stable_frames[k]+=1
                if self._stable_frames[k]>=self.STABLE_FRAMES_REQUIRED:
                    self._ready[k]=True; self.stages[k]="up"
            else:
                self._stable_frames[k]=0
            return False
        if angle<ex["down_angle"]:
            if st!="down":
                self.stages[k]="down"; self._rep_start_time[k]=time.time()
                self._min_angle_in_rep[k]=angle
            elif angle<self._min_angle_in_rep[k]:
                self._min_angle_in_rep[k]=angle
        if angle>ex["up_angle"] and st=="down":
            self.stages[k]="up"; self.reps[k]+=1
            self._rep_roms[k].append(self._min_angle_in_rep[k])
            if self._rep_start_time[k] is not None:
                self._rep_speeds[k].append(time.time()-self._rep_start_time[k])
            self._rep_start_time[k]=None; self._min_angle_in_rep[k]=180.0
            return True
        return False

    def _calc_speed_score(self) -> float:
        speeds=self._rep_speeds[self.ck]
        if not speeds: return 50.0
        lo,hi=self.ce["ideal_rep_seconds"]; scores=[]
        for dur in speeds:
            if lo<=dur<=hi:    scores.append(100.)
            elif dur<lo:       scores.append(max(0.,100.-(lo-dur)/0.5*10))
            else:              scores.append(max(0.,100.-(dur-hi)/0.5*8))
        return round(sum(scores)/len(scores),1)

    def _calc_rom_score(self) -> float:
        roms=self._rep_roms[self.ck]
        if not roms: return 50.0
        target=self.ce["down_angle"]
        return round(sum(max(0.,100.-max(0.,ma-target)*2) for ma in roms)/len(roms),1)

    def get_cam_data(self) -> dict:
        return {
            "reps_completed": self.reps[self.ck],
            "speed_score":    self._calc_speed_score(),
            "rom_score":      self._calc_rom_score(),
        }

    def draw_skeleton(self, frame, lms, wc, hc, off):
        if not DISPLAY_CONFIG["show_skeleton"] or lms is None: return
        ox,oy=off
        for a,b in POSE_CONNECTIONS:
            if a>=len(lms) or b>=len(lms): continue
            la=lms[a]; lb=lms[b]
            if la.visibility<0.4 or lb.visibility<0.4: continue
            cv2.line(frame,(int(la.x*wc)+ox,int(la.y*hc)+oy),
                           (int(lb.x*wc)+ox,int(lb.y*hc)+oy),C["skeleton"],2)
        for lm in lms:
            if lm.visibility<0.4: continue
            p=(int(lm.x*wc)+ox,int(lm.y*hc)+oy)
            cv2.circle(frame,p,4,C["joint"],-1)
            cv2.circle(frame,p,4,C["white"],1)

    def draw_ui(self, frame, flash=False):
        H,W=frame.shape[:2]; k=self.ck
        rounded_rect(frame,10,10,300,200,12,C["bg"],0.75)
        y=45
        if DISPLAY_CONFIG["show_exercise_name"]:
            n=self.ce["name"]; txt(frame,n[:22],(20,y),0.52,C["accent"],2); y+=22
        if DISPLAY_CONFIG["show_rep_count"]:
            txt(frame,f"REP: {self.reps[k]}",(20,y+30),1.4,
                C["yellow"] if flash else C["green"],3); y+=65
        if not self._ready[k]:
            pct=int((self._stable_frames[k]/self.STABLE_FRAMES_REQUIRED)*80)
            cv2.rectangle(frame,(20,y),(100,y+12),(60,60,60),-1)
            cv2.rectangle(frame,(20,y),(20+pct,y+12),C["orange"],-1)
            txt(frame,f"Stabilizing: {self._stable_frames[k]}/{self.STABLE_FRAMES_REQUIRED}",
                (20,y+26),0.45,C["orange"],1); y+=40
        else:
            txt(frame,"READY",(20,y),0.6,C["green"],2); y+=24
        if DISPLAY_CONFIG["show_stage"]:
            st=(self.stages[k] or "---").upper()
            txt(frame,f"Stage: {st}",(20,y),0.65,
                C["green"] if st=="UP" else C["orange"],2); y+=28
        if DISPLAY_CONFIG["show_angle"]:
            txt(frame,f"Angle: {int(self.angle)}",(20,y),0.65,C["yellow"],2); y+=28
        if DISPLAY_CONFIG["show_timer"]:
            tl="PAUSED" if self.paused else fmt_time(self.elapsed())
            txt(frame,f"Time: {tl}",(20,y),0.65,
                C["red"] if self.paused else C["cyan"],2)
        if DISPLAY_CONFIG["show_fps"]:
            txt(frame,f"FPS: {self.fps}",(W-120,35),0.65,C["white"],2)
        ex=self.ce; bx,by,bw,bh=20,H-50,260,16
        norm=np.clip((self.angle-ex["down_angle"]+10)/(ex["up_angle"]-ex["down_angle"]+20),0,1)
        rounded_rect(frame,bx-4,by-4,bw+8,bh+8,6,C["bg"],0.7)
        cv2.rectangle(frame,(bx,by),(bx+bw,by+bh),(60,60,60),-1)
        cv2.rectangle(frame,(bx,by),(bx+int(norm*bw),by+bh),
                      C["green"] if self.stages[k]=="up" else C["orange"],-1)
        txt(frame,"Angle range",(bx,by-8),0.45,C["white"],1)
        if flash:
            ov=frame.copy()
            cv2.rectangle(ov,(0,0),(W,H),C["green"],-1)
            cv2.addWeighted(ov,0.12,frame,0.88,0,frame)

    def run(self):
        """
        Vòng lặp chính — chạy trong thread riêng.
        KHÔNG có cv2.imshow(), KHÔNG có cv2.waitKey().
        Frame được đẩy sang MJPEG server qua _set_frame().
        """
        # Xoa frame cu cua buoi truoc -> hien placeholder "dang khoi dong"
        _ph = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), np.uint8)
        txt(_ph, "Dang khoi dong camera...", (60, FRAME_HEIGHT // 2), 1.0, C["white"], 2)
        _set_frame(_ph)

        cap=cv2.VideoCapture(CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        if not cap.isOpened():
            _err = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), np.uint8)
            txt(_err, "KHONG MO DUOC CAMERA", (60, FRAME_HEIGHT // 2), 1.0, C["red"], 2)
            _set_frame(_err)
            print("[ERR] Cannot open camera."); return

        print("[CAM] Loop started (streaming via MJPEG)...")
        flash_n=0

        while not self._stop:
            ret,frame=cap.read()
            if not ret: time.sleep(0.05); continue

            frame=cv2.flip(frame,1)
            frame=cv2.LUT(frame, _GAMMA_LUT)
            self.update_fps()

            if self.paused:
                self.draw_ui(frame)
                _set_frame(frame)
                time.sleep(0.033)
                continue

            timestamp_ms=int(time.time()*1000)

            # YOLO
            best_box=None; best_conf=0.0
            yres=self.yolo(frame,classes=[0],verbose=False)[0]
            for box in yres.boxes:
                conf=float(box.conf[0])
                if conf>best_conf: best_conf=conf; best_box=tuple(map(int,box.xyxy[0]))
            if best_box and DISPLAY_CONFIG["show_bounding_box"]:
                x1,y1,x2,y2=best_box
                cv2.rectangle(frame,(x1,y1),(x2,y2),C["accent"],2)

            # MediaPipe
            try:
                lms,wc,hc,off=self.detect_pose(frame,timestamp_ms,best_box)
            except RuntimeError as e:
                if "shutdown" in str(e):   # app dang dong -> MediaPipe da tat worker, thoat em
                    break
                raise
            new_rep=False

            if lms:
                ji=self.ce["joints"]; ox,oy=off
                def lm_px(i):
                    lm=lms[i]; return (int(lm.x*wc)+ox,int(lm.y*hc)+oy)
                pa,pb,pc=lm_px(ji[0]),lm_px(ji[1]),lm_px(ji[2])
                self.angle=calc_angle(pa,pb,pc)
                new_rep=self.count_rep(self.angle)
                if new_rep:
                    flash_n=5
                    if self.on_rep:
                        try: self.on_rep(self.reps[self.ck], sum(self.reps.values()))
                        except Exception: pass
                cv2.line(frame,pb,pa,C["yellow"],2)
                cv2.line(frame,pb,pc,C["yellow"],2)
                cv2.circle(frame,pb,8,C["yellow"],-1)
                if DISPLAY_CONFIG["show_angle"]:
                    txt(frame,f"{int(self.angle)}",(pb[0]+12,pb[1]-12),0.65,C["yellow"],2)
                self.draw_skeleton(frame,lms,wc,hc,off)

            if flash_n>0: flash_n-=1
            self.draw_ui(frame,flash=(flash_n>0))
            _set_frame(frame)   # ← thay thế cv2.imshow()

        cap.release()
        print("[CAM] Stopped.")