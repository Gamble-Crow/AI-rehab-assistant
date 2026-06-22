"""
camera.py — WorkoutTracker + MJPEG server ngầm
================================================
- Không còn cv2.imshow() / cửa sổ riêng
- MJPEG server chạy ngầm trên http://127.0.0.1:MJPEG_PORT/video
- app.html nhúng: <img src="http://127.0.0.1:8765/video">
- Pose bằng MediaPipe (đã bỏ YOLO); góc khớp tính từ landmark 3D
- Giữ nguyên get_cam_data()
"""

import cv2
import mediapipe as mp
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode
import time
import urllib.request
from urllib.parse import urlparse, parse_qs, unquote
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

from app import config

MJPEG_PORT   = 8765
CAMERA_INDEX = 0
FRAME_WIDTH  = 1280
FRAME_HEIGHT = 720

# Tang sang phong toi bang gamma (chinh tay): <1 = sang hon, lift vung toi manh
CAM_GAMMA  = 0.78
_GAMMA_LUT = np.array([((i / 255.0) ** CAM_GAMMA) * 255 for i in range(256)], dtype=np.uint8)

DISPLAY_CONFIG = {
    "show_angle":    True,   # ve so do goc tai khop tren camera
    "show_skeleton": True,   # ve khung xuong
}

POSE_CONNECTIONS = [
    (11,12),(11,13),(13,15),(12,14),(14,16),
    (11,23),(12,24),(23,24),
    (23,25),(25,27),(24,26),(26,28),
]

C = {
    "white":(255,255,255), "green":(0,210,90), "yellow":(0,220,230),
    "red":(0,60,220), "skeleton":(50,200,50), "joint":(0,140,255),
}

# ── Cache model MediaPipe: nap 1 lan, dung lai moi buoi ──
_LANDMARKER = None

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
        rel  = unquote((parse_qs(urlparse(self.path).query).get("f") or [""])[0])
        path = os.path.normpath(os.path.join(config.VIDEO_DIR, os.path.basename(rel)))
        vdir = config.VIDEO_DIR
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

def calc_angle_3d(a, b, c):
    # Goc tu landmark 3D (x,y,z met) -> dung that, khong phu thuoc goc dat camera
    a=np.array([a.x,a.y,a.z]); b=np.array([b.x,b.y,b.z]); c=np.array([c.x,c.y,c.z])
    ba=a-b; bc=c-b
    cos=np.dot(ba,bc)/(np.linalg.norm(ba)*np.linalg.norm(bc)+1e-6)
    return float(np.degrees(np.arccos(np.clip(cos,-1.,1.))))

# ── Vẽ chữ Unicode (tiếng Việt) bằng PIL ──
_FONT_FILE  = "C:/Windows/Fonts/arial.ttf"
_FONT_CACHE = {}
def _font(px):
    f = _FONT_CACHE.get(px)
    if f is None:
        try:    f = ImageFont.truetype(_FONT_FILE, px)
        except Exception: f = ImageFont.load_default()
        _FONT_CACHE[px] = f
    return f

def _draw_texts(img, items):
    # items: [(text, (x, y_baseline), scale, color_bgr, thick), ...] — vẽ 1 lần/khung
    try:
        pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        d   = ImageDraw.Draw(pil)
        for text, pos, scale, color, thick in items:
            px  = max(13, int(scale * 34))
            rgb = (int(color[2]), int(color[1]), int(color[0]))   # BGR -> RGB
            d.text((pos[0], pos[1] - px), str(text), font=_font(px), fill=rgb,
                   stroke_width=max(1, thick - 1), stroke_fill=(0, 0, 0))
        img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    except Exception:
        pass

def txt(img, text, pos, scale, color, thick=2):
    _draw_texts(img, [(text, pos, scale, color, thick)])

def ensure_model(path=None):
    if path is None: path = config.model("pose_landmarker_full.task")
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
        self.landmarker = _get_landmarker()

        self._cfg = config
        self._k   = "cur"               # 1 bai / buoi -> 1 key duy nhat
        keys      = [self._k]

        self.reps   = {k: 0    for k in keys}
        self.stages = {k: None for k in keys}
        self.angle  = 0.0

        self.t0=None;        self.paused=False   # t0 dat khi khung hinh dau tien ve (camera that su mo)
        self.pause_acc=0.0;  self.pause_start=0.0
        self.fps=0;          self.ftimes=[]
        self.on_ready=None   # callback goi 1 lan khi camera san sang (frame dau tien)

        self._rep_start_time   = {k: None  for k in keys}
        self._min_angle_in_rep = {k: 180.0 for k in keys}
        self._rep_speeds       = {k: []    for k in keys}
        self._rep_roms         = {k: []    for k in keys}

        self.STABLE_FRAMES_REQUIRED = 5
        self._stable_frames = {k: 0     for k in keys}
        self._ready         = {k: False for k in keys}

        self._stop  = False
        self.on_rep = None   # callback(rep_current, total)
        self.on_stats = None # callback(rep,total,phase,angle,ready) - day so lieu sang UI (throttle)
        self._last_stats = 0.0

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
        if self.t0 is None: return 0.0           # camera chua mo -> chua dem gio
        base=self.pause_start if self.paused else time.time()
        return base-self.t0-self.pause_acc

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
            world = res.pose_world_landmarks[0] if res.pose_world_landmarks else None
            return res.pose_landmarks[0], world, w_c, h_c, off
        return None, None, w_c, h_c, off

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
        # Thong tin buoi tap (so lan/pha/goc/dau) da chuyen sang cot phai cua giao dien (HTML).
        # Tren camera chi giu hieu ung flash xanh khi co rep moi -> khung hinh thoang.
        if flash:
            H,W=frame.shape[:2]
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
        txt(_ph, "Đang khởi động camera...", (60, FRAME_HEIGHT // 2), 1.0, C["white"], 2)
        _set_frame(_ph)

        cap=cv2.VideoCapture(CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        if not cap.isOpened():
            _err = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 3), np.uint8)
            txt(_err, "Không mở được camera", (60, FRAME_HEIGHT // 2), 1.0, C["red"], 2)
            _set_frame(_err)
            print("[ERR] Cannot open camera."); return

        print("[CAM] Loop started (streaming via MJPEG)...")
        flash_n=0

        while not self._stop:
            ret,frame=cap.read()
            if not ret: time.sleep(0.05); continue

            if self.t0 is None:                 # khung hinh dau tien -> camera that su mo
                self.t0=time.time()
                if self.on_ready:
                    try: self.on_ready()
                    except Exception: pass

            frame=cv2.flip(frame,1)
            frame=cv2.LUT(frame, _GAMMA_LUT)
            self.update_fps()

            if self.paused:
                self.draw_ui(frame)
                _set_frame(frame)
                time.sleep(0.033)
                continue

            timestamp_ms=int(time.time()*1000)

            # MediaPipe Pose tren TOAN khung (da bo YOLO)
            try:
                lms,lms_world,wc,hc,off=self.detect_pose(frame,timestamp_ms)
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
                # Goc tu landmark 3D (chuan, khong phu thuoc goc dat camera); du phong 2D
                if lms_world is not None:
                    self.angle=calc_angle_3d(lms_world[ji[0]],lms_world[ji[1]],lms_world[ji[2]])
                else:
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

            now=time.time()
            if self.on_stats and now-self._last_stats>=0.2:
                self._last_stats=now
                k=self.ck; st=self.stages[k]
                if not self._ready[k]:  phase="Đang ổn định"
                elif st=="up":          phase="Duỗi"
                elif st=="down":        phase="Gập"
                else:                   phase="—"
                try: self.on_stats(self.reps[k], sum(self.reps.values()), phase, int(self.angle), self._ready[k])
                except Exception: pass

            if flash_n>0: flash_n-=1
            self.draw_ui(frame,flash=(flash_n>0))
            _set_frame(frame)   # ← thay thế cv2.imshow()

        cap.release()
        print("[CAM] Stopped.")