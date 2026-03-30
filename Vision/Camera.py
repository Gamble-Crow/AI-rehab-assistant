import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from mediapipe.tasks.python.vision import PoseLandmarker, PoseLandmarkerOptions, RunningMode
from ultralytics import YOLO
import time
import urllib.request
import os

# CẤU HÌNH HIỂN THỊ 
DISPLAY_CONFIG = {
    "show_exercise_name":   True,
    "show_rep_count":       True,
    "show_stage":           True,
    "show_angle":           True,
    "show_fps":             True,
    "show_skeleton":        True,
    "show_bounding_box":    True,
    "show_instructions":    True,
    "show_timer":           True,
    "show_confidence":      False,
}

#bai tap mac dinh khi run
DEFAULT_EXERCISE = "squat"

# cau hinh camera
CAMERA_INDEX = 0
FRAME_WIDTH  = 1280
FRAME_HEIGHT = 720

#
#  Thông số bài tập
#  joints: 3 điểm tính góc góc (a, b, c) — b là đỉnh góc
#
#  Index MediaPipe 0.10.x:
#    11=left_shoulder  12=right_shoulder
#    13=left_elbow     14=right_elbow
#    15=left_wrist     16=right_wrist
#    23=left_hip       24=right_hip
#    25=left_knee      26=right_knee
#    27=left_ankle     28=right_ankle
#
EXERCISES = {
    "squat": {
        "name":        "SQUAT (Dung len ngoi xuong)",
        "joints":      (23, 25, 27),   # left_hip, left_knee, left_ankle
        "down_angle":  90,
        "up_angle":    160,
        "description": "Dung thang -> Ngoi xuong (goi 90 do) -> Dung len",
    },
    "pushup": {
        "name":        "PUSH-UP (Hit dat)",
        "joints":      (11, 13, 15),   # left_shoulder, left_elbow, left_wrist
        "down_angle":  70,
        "up_angle":    160,
        "description": "Nam sap -> Ha nguoi xuong -> Day len",
    },
    "lunge": {
        "name":        "LUNGE (Buoc chan truoc)",
        "joints":      (23, 25, 27),
        "down_angle":  85,
        "up_angle":    160,
        "description": "Dung thang -> Buoc 1 chan -> Ha thap nguoi",
    },
    "situp": {
        "name":        "SIT-UP (Gap bung)",
        "joints":      (11, 23, 25),   # left_shoulder, left_hip, left_knee
        "down_angle":  55,
        "up_angle":    120,
        "description": "Nam ngua -> Gap nguoi len -> Nam xuong",
    },
    # Them bai tap moi o day:
    # "bicep_curl": {
    #     "name":       "BICEP CURL (Curl tay)",
    #     "joints":     (11, 13, 15),
    #     "down_angle": 160,
    #     "up_angle":   30,
    #     "description": "Tay thang -> Cuon len -> Tha xuong",
    # },
}

# Danh sách các cặp nối xương để vẽ xương
POSE_CONNECTIONS = [
    (11,12),(11,13),(13,15),(12,14),(14,16),
    (11,23),(12,24),(23,24),
    (23,25),(25,27),(24,26),(26,28),
    (27,29),(28,30),(29,31),(30,32),
]

# màu sắc
C = {
    "white":    (255, 255, 255),
    "black":    (0,   0,   0),
    "green":    (0,   210, 90),
    "yellow":   (0,   220, 230),
    "cyan":     (220, 200, 0),
    "orange":   (0,   140, 255),
    "red":      (0,   60,  220),
    "bg":       (30,  30,  30),
    "accent":   (0,   180, 255),
    "skeleton": (50,  200, 50),
    "joint":    (0,   140, 255),
}

# tinh goc
def calc_angle(a, b, c):
    a = np.array(a); b = np.array(b); c = np.array(c)
    ba = a - b; bc = c - b
    cos = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-6)
    return float(np.degrees(np.arccos(np.clip(cos, -1.0, 1.0))))


def rounded_rect(img, x, y, w, h, r, color, alpha=0.7):
    ov = img.copy()
    cv2.rectangle(ov, (x+r, y),   (x+w-r, y+h),   color, -1)
    cv2.rectangle(ov, (x,   y+r), (x+w,   y+h-r), color, -1)
    for cx, cy in [(x+r,y+r),(x+w-r,y+r),(x+r,y+h-r),(x+w-r,y+h-r)]:
        cv2.circle(ov, (cx, cy), r, color, -1)
    cv2.addWeighted(ov, alpha, img, 1-alpha, 0, img)


def txt(img, text, pos, scale, color, thick=2):
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, C["black"], thick+2, cv2.LINE_AA)
    cv2.putText(img, text, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color,     thick,   cv2.LINE_AA)


def fmt_time(s):
    return f"{int(s)//60:02d}:{int(s)%60:02d}"


def ensure_model(path="pose_landmarker_full.task"):
    if os.path.exists(path):
        return path
    url = ("https://storage.googleapis.com/mediapipe-models/"
           "pose_landmarker/pose_landmarker_full/float16/latest/"
           "pose_landmarker_full.task")
    print("[INFO] Dang tai MediaPipe model (~30MB)...")
    urllib.request.urlretrieve(url, path)
    print(f"[INFO] Da tai xong: {path}")
    return path


# workoutTracker
class WorkoutTracker:

    def __init__(self):
        # YOLO
        print("[INFO] Dang tai YOLO model...")
        self.yolo = YOLO("yolov8n.pt")

        # mediaPipe 0.10.x — tasks api
        print("[INFO] Dang khoi dong MediaPipe Pose (0.10.x Tasks API)...")
        model_path = ensure_model()
        base_opts  = mp_python.BaseOptions(model_asset_path=model_path)
        opts = PoseLandmarkerOptions(
            base_options=base_opts,
            running_mode=RunningMode.IMAGE,
            num_poses=1,
            min_pose_detection_confidence=0.6,
            min_pose_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.landmarker = PoseLandmarker.create_from_options(opts)

        # trạng thái
        self.ex_keys    = list(EXERCISES.keys())
        self.cur_idx    = self.ex_keys.index(DEFAULT_EXERCISE) if DEFAULT_EXERCISE in EXERCISES else 0
        self.reps       = {k: 0    for k in EXERCISES}
        self.stages     = {k: None for k in EXERCISES}
        self.angle      = 0.0

        # thời gian
        self.t0          = time.time()
        self.paused      = False
        self.pause_acc   = 0.0
        self.pause_start = 0.0

        # fps
        self.fps         = 0
        self.ftimes      = []

        print("[INFO] San sang!")

    @property
    def ck(self):   return self.ex_keys[self.cur_idx]
    @property
    def ce(self):   return EXERCISES[self.ck]

    def switch(self, d):
        self.cur_idx = (self.cur_idx + d) % len(self.ex_keys)

    def reset(self):
        self.reps[self.ck] = 0; self.stages[self.ck] = None

    def toggle_pause(self):
        if not self.paused:
            self.paused = True;  self.pause_start = time.time()
        else:
            self.paused = False; self.pause_acc += time.time() - self.pause_start

    def elapsed(self):
        base = self.pause_start if self.paused else time.time()
        return base - self.t0 - self.pause_acc

    def update_fps(self):
        now = time.time(); self.ftimes.append(now)
        self.ftimes = [t for t in self.ftimes if now-t < 1.0]
        self.fps = len(self.ftimes)

    def detect_pose(self, frame, box=None):
        H, W = frame.shape[:2]
        if box:
            x1,y1,x2,y2 = box
            pad = 30
            x1=max(0,x1-pad); y1=max(0,y1-pad)
            x2=min(W,x2+pad); y2=min(H,y2+pad)
            crop = frame[y1:y2, x1:x2]; off = (x1, y1)
        else:
            crop = frame; off = (0, 0)

        h_c, w_c = crop.shape[:2]
        rgb    = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res    = self.landmarker.detect(mp_img)

        if res.pose_landmarks and len(res.pose_landmarks) > 0:
            return res.pose_landmarks[0], w_c, h_c, off
        return None, w_c, h_c, off

    def count_rep(self, angle):
        ex = self.ce; k = self.ck; st = self.stages[k]
        if angle < ex["down_angle"]:
            self.stages[k] = "down"
        if angle > ex["up_angle"] and st == "down":
            self.stages[k] = "up"; self.reps[k] += 1; return True
        return False

    def draw_skeleton(self, frame, lms, wc, hc, off):
        if not DISPLAY_CONFIG["show_skeleton"] or lms is None: return
        ox, oy = off
        for a,b in POSE_CONNECTIONS:
            if a >= len(lms) or b >= len(lms): continue
            la = lms[a]; lb = lms[b]
            if la.visibility < 0.4 or lb.visibility < 0.4: continue
            ax=int(la.x*wc)+ox; ay=int(la.y*hc)+oy
            bx=int(lb.x*wc)+ox; by=int(lb.y*hc)+oy
            cv2.line(frame,(ax,ay),(bx,by),C["skeleton"],2)
        for lm in lms:
            if lm.visibility < 0.4: continue
            px=int(lm.x*wc)+ox; py=int(lm.y*hc)+oy
            cv2.circle(frame,(px,py),4,C["joint"],-1)
            cv2.circle(frame,(px,py),4,C["white"],1)

    def draw_ui(self, frame, flash=False):
        H, W = frame.shape[:2]
        k = self.ck

        # Panel chính
        rounded_rect(frame, 10, 10, 300, 215, 12, C["bg"], 0.75)
        y = 45

        if DISPLAY_CONFIG["show_exercise_name"]:
            n = self.ce["name"]
            txt(frame, n[:22], (20, y), 0.52, C["accent"], 2); y += 22
            if len(n) > 22:
                txt(frame, n[22:], (20, y), 0.52, C["accent"], 2); y += 22

        if DISPLAY_CONFIG["show_rep_count"]:
            rc = C["yellow"] if flash else C["green"]
            txt(frame, f"REP: {self.reps[k]}", (20, y+30), 1.4, rc, 3); y += 65

        if DISPLAY_CONFIG["show_stage"]:
            st = (self.stages[k] or "---").upper()
            sc = C["green"] if st == "UP" else C["orange"]
            txt(frame, f"Stage: {st}", (20, y), 0.65, sc, 2); y += 28

        if DISPLAY_CONFIG["show_angle"]:
            txt(frame, f"Angle: {int(self.angle)}", (20, y), 0.65, C["yellow"], 2); y += 28

        if DISPLAY_CONFIG["show_timer"]:
            tl = "PAUSED" if self.paused else fmt_time(self.elapsed())
            tc = C["red"] if self.paused else C["cyan"]
            txt(frame, f"Time: {tl}", (20, y), 0.65, tc, 2)

        if DISPLAY_CONFIG["show_fps"]:
            txt(frame, f"FPS: {self.fps}", (W-120, 35), 0.65, C["white"], 2)

        # angle bar
        ex = self.ce
        bx, by, bw, bh = 20, H-50, 260, 16
        norm   = np.clip((self.angle - ex["down_angle"]+10) / (ex["up_angle"]-ex["down_angle"]+20), 0, 1)
        filled = int(norm * bw)
        rounded_rect(frame, bx-4, by-4, bw+8, bh+8, 6, C["bg"], 0.7)
        cv2.rectangle(frame, (bx,by), (bx+bw,by+bh), (60,60,60), -1)
        bc = C["green"] if self.stages[k] == "up" else C["orange"]
        cv2.rectangle(frame, (bx,by), (bx+filled,by+bh), bc, -1)
        txt(frame, "Angle range", (bx, by-8), 0.45, C["white"], 1)

        # hướng dẫn
        if DISPLAY_CONFIG["show_instructions"]:
            for i, t in enumerate(["Q/ESC: Thoat", "N: Tiep  P: Truoc", "R: Reset  Space: Pause"]):
                txt(frame, t, (20, H-95+i*18), 0.42, C["white"], 1)

        # danh sách bài tập
        pw = 230; rh = 26; pad = 10
        n  = len(EXERCISES)
        px = W - pw - 10; py = H - n*rh - pad*2 - 10
        rounded_rect(frame, px, py, pw, n*rh+pad*2, 10, C["bg"], 0.7)
        for i, (ek, ev) in enumerate(EXERCISES.items()):
            ey  = py + pad + i*rh + 18
            isc = (ek == k)
            col = C["accent"] if isc else C["white"]
            sn  = ev["name"].split("(")[0].strip()
            txt(frame, f"{'> ' if isc else '  '}{sn}  [{self.reps[ek]}]",
                (px+10, ey), 0.48, col, 1)

        # báo xanh khi rep mới
        if flash:
            ov = frame.copy()
            cv2.rectangle(ov, (0,0), (W,H), C["green"], -1)
            cv2.addWeighted(ov, 0.12, frame, 0.88, 0, frame)

    def run(self):
        # --- mở camera với backend DirectShow (Windows) cho camera USB ---
        print(f"[INFO] Dang mo camera {CAMERA_INDEX}...")
        cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)  # chọn camera 
        if not cap.isOpened():
            print("[INFO] Thu lai khong dung CAP_DSHOW...")
            cap = cv2.VideoCapture(1)  # fallback không dùng backend

        if not cap.isOpened():
            print(f"[LOI] Khong mo duoc camera {CAMERA_INDEX}.")
            print("      Kiem tra lai ket noi camera hoac doi CAMERA_INDEX.")
            return

        # cấu hình camera
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # giảm buffer lag

        # khởi động: đọc vài frame đầu để camera ổn định
        print("[INFO] Dang khoi dong camera (warm-up)...")
        for _ in range(5):
            cap.read()
            time.sleep(0.05)

        # kiểm tra đọc được frame chưa
        ret, test_frame = cap.read()
        if not ret or test_frame is None:
            print(f"[LOI] Camera {CAMERA_INDEX} mo duoc nhung khong doc duoc frame.")
            print("      Thu doi CAMERA_INDEX = 0.")
            cap.release()
            return

        actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        print(f"[INFO] Camera san sang: {actual_w}x{actual_h}")

        print("\n**************************************")
        print("  AI WORKOUT TRACKER san sang!")
        print("  N / P  -> Doi bai tap")
        print("  R      -> Reset rep")
        print("  Space  -> Pause")
        print("  Q/ESC  -> Thoat")
        print("**************************************\n")

        flash_n = 0

        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                # camera USB doi khi drop frame — bo qua thay vi thoat
                time.sleep(0.01)
                continue
            frame = cv2.flip(frame, 1)
            self.update_fps()

            if self.paused:
                self.draw_ui(frame)
                cv2.imshow("AI Workout Tracker", frame)
                key = cv2.waitKey(30) & 0xFF
                if key in (ord('q'), 27): break
                elif key == ord(' '): self.toggle_pause()
                continue

            # yOLO
            best_box  = None; best_conf = 0.0
            yres      = self.yolo(frame, classes=[0], verbose=False)[0]
            for box in yres.boxes:
                conf = float(box.conf[0])
                if conf > best_conf:
                    best_conf = conf
                    best_box  = tuple(map(int, box.xyxy[0]))

            if best_box and DISPLAY_CONFIG["show_bounding_box"]:
                x1,y1,x2,y2 = best_box
                cv2.rectangle(frame, (x1,y1), (x2,y2), C["accent"], 2)
                if DISPLAY_CONFIG["show_confidence"]:
                    txt(frame, f"{best_conf:.2f}", (x1,y1-8), 0.55, C["accent"], 2)

            # mediaPipe
            lms, wc, hc, off = self.detect_pose(frame, best_box)
            new_rep = False

            if lms:
                ji     = self.ce["joints"]
                ox, oy = off

                def lm_px(i):
                    lm = lms[i]
                    return (int(lm.x*wc)+ox, int(lm.y*hc)+oy)

                pa, pb, pc = lm_px(ji[0]), lm_px(ji[1]), lm_px(ji[2])
                self.angle = calc_angle(pa, pb, pc)
                new_rep    = self.count_rep(self.angle)
                if new_rep: flash_n = 5

                # Vẽ góc
                cv2.line(frame, pb, pa, C["yellow"], 2)
                cv2.line(frame, pb, pc, C["yellow"], 2)
                cv2.circle(frame, pb, 8, C["yellow"], -1)
                if DISPLAY_CONFIG["show_angle"]:
                    txt(frame, f"{int(self.angle)}", (pb[0]+12, pb[1]-12), 0.65, C["yellow"], 2)

                self.draw_skeleton(frame, lms, wc, hc, off)

            if flash_n > 0: flash_n -= 1
            self.draw_ui(frame, flash=(flash_n > 0))
            cv2.imshow("AI Workout Tracker", frame)

            key = cv2.waitKey(1) & 0xFF
            if key in (ord('q'), 27):   break
            elif key == ord('n'):       self.switch(+1)
            elif key == ord('p'):       self.switch(-1)
            elif key == ord('r'):       self.reset()
            elif key == ord(' '):       self.toggle_pause()

        cap.release()
        cv2.destroyAllWindows()
        print("\n========== KET QUA BUOI TAP ==========")
        print(f"Thoi gian: {fmt_time(self.elapsed())}")
        for k, v in EXERCISES.items():
            if self.reps[k] > 0:
                print(f"  {v['name']}: {self.reps[k]} rep")
        print("=======================================\n")

if __name__ == "__main__":
    tracker = WorkoutTracker()
    tracker.run()
