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

DEFAULT_EXERCISE = "Gap/duoi khuyu tay"

CAMERA_INDEX = 0
FRAME_WIDTH  = 1280
FRAME_HEIGHT = 720

#   ĐỊNH NGHĨA BÀI TẬP 
#  joints: 3 landmark index đe tinh goc (a, b, c)
#
#  Index MediaPipe 0.10.x:
#    11=vai phải        12=vai trái
#    13=khuỷu tay phải  14=khuỷu tay trái
#    15=cổ tay phải     16=cổ tay trái
#    23=hông phải       24=hông trái
#    25=đầu gối phải    26=đầu gối trái
#    27=cổ chân phải    28=cổ chân trái

EXERCISES = {
    "squat": {
        "name":              "SQUAT (Dung len ngoi xuong)",
        "joints":            (23, 25, 27),
        "down_angle":        90,
        "up_angle":          160,
        "description":       "Dung thang -> Ngoi xuong (goi 90 do) -> Dung len",
        "ideal_rep_seconds": (2.5, 5.0),  # (min, max) giay/rep hop le
    },
    "pushup": {
        "name":              "PUSH-UP (Hit dat)",
        "joints":            (11, 13, 15),
        "down_angle":        70,
        "up_angle":          160,
        "description":       "Nam sap -> Ha nguoi xuong -> Day len",
        "ideal_rep_seconds": (2.0, 4.0),
    },
    "lunge": {
        "name":              "LUNGE (Buoc chan truoc)",
        "joints":            (23, 25, 27),
        "down_angle":        85,
        "up_angle":          160,
        "description":       "Dung thang -> Buoc 1 chan -> Ha thap nguoi",
        "ideal_rep_seconds": (2.5, 5.0),
    },
    "Gap/duoi khuyu tay": {
        "name":              "Gap/duoi khuyu tay",
        "joints":            (12, 14, 16),
        "down_angle":        35,
        "up_angle":          165,
        "description":       "Gap khuyu tay. Tay de tren mat phang",
        "ideal_rep_seconds": (2.0, 4.0),
    },
}

# Danh sach cac cap noi xưong
POSE_CONNECTIONS = [
    (11,12),(11,13),(13,15),(12,14),(14,16),
    (11,23),(12,24),(23,24),
    (23,25),(25,27),(24,26),(26,28),
    (27,29),(28,30),(29,31),(30,32),
]

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

class WorkoutTracker:

    def __init__(self):
        # YOLO
        print("[INFO] Dang tai YOLO model...")
        self.yolo = YOLO("yolov8n.pt")

        # MediaPipe 0.10.x — Tasks API
        print("[INFO] Dang khoi dong MediaPipe Pose (0.10.x Tasks API)...")
        model_path = ensure_model()
        base_opts  = mp_python.BaseOptions(model_asset_path=model_path)
        opts = PoseLandmarkerOptions(
            base_options=base_opts,
            running_mode=RunningMode.VIDEO,   # VIDEO: dùng timestamp, tracking liên tục giữa các frame
            num_poses=1,
            min_pose_detection_confidence=0.6,
            min_pose_presence_confidence=0.6,
            min_tracking_confidence=0.6,
        )
        self.landmarker = PoseLandmarker.create_from_options(opts)

        # Trạng thái
        self.ex_keys    = list(EXERCISES.keys())
        self.cur_idx    = self.ex_keys.index(DEFAULT_EXERCISE) if DEFAULT_EXERCISE in EXERCISES else 0
        self.reps       = {k: 0    for k in EXERCISES}
        self.stages     = {k: None for k in EXERCISES}
        self.angle      = 0.0

        # Timer
        self.t0          = time.time()
        self.paused      = False
        self.pause_acc   = 0.0
        self.pause_start = 0.0

        # FPS
        self.fps         = 0
        self.ftimes      = []

        # Tracking speed & ROM theo tung rep (cho brain_engine)
        # _rep_start_time : thoi diem bat dau xuong (vao giai doan "down")
        # _min_angle_in_rep: goc nho nhat dat duoc trong giai doan "down"
        # _rep_speeds     : danh sach thoi gian (giay) cua tung rep da hoan thanh
        # _rep_roms       : danh sach goc nho nhat (do) cua tung rep da hoan thanh
        self._rep_start_time:  dict[str, float | None] = {k: None for k in EXERCISES}
        self._min_angle_in_rep: dict[str, float]        = {k: 180.0 for k in EXERCISES}
        self._rep_speeds:       dict[str, list[float]]  = {k: []    for k in EXERCISES}
        self._rep_roms:         dict[str, list[float]]  = {k: []    for k in EXERCISES}

        # Stabilization Gate: yeu cau giu vi tri bat dau (up_angle) on dinh
        # STABLE_FRAMES_REQUIRED frame lien tiep truoc khi bat dau dem rep.
        # ~20 frame @ 20fps ≈ 1 giay giu vi tri → du de loc nhieu khi dat camera.
        self.STABLE_FRAMES_REQUIRED = 20
        self._stable_frames: dict[str, int]  = {k: 0     for k in EXERCISES}
        self._ready:         dict[str, bool] = {k: False for k in EXERCISES}

        print("[INFO] San sang!")

    @property
    def ck(self):   return self.ex_keys[self.cur_idx]
    @property
    def ce(self):   return EXERCISES[self.ck]

    def switch(self, d):
        self.cur_idx = (self.cur_idx + d) % len(self.ex_keys)
        # Reset stabilization cho bai tap moi de tranh dem rep thừa khi doi bai
        k = self.ck
        self._stable_frames[k] = 0
        self._ready[k]         = False
        self.stages[k]         = None

    def reset(self):
        k = self.ck
        self.reps[k]   = 0
        self.stages[k] = None
        self._rep_start_time[k]   = None
        self._min_angle_in_rep[k] = 180.0
        self._rep_speeds[k].clear()
        self._rep_roms[k].clear()
        self._stable_frames[k] = 0
        self._ready[k]         = False

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

    def detect_pose(self, frame, timestamp_ms, box=None):
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

        # VIDEO mode: phai truyen timestamp tang dan (ms)
        res = self.landmarker.detect_for_video(mp_img, timestamp_ms)

        if res.pose_landmarks and len(res.pose_landmarks) > 0:
            return res.pose_landmarks[0], w_c, h_c, off
        return None, w_c, h_c, off

    def count_rep(self, angle):
        ex = self.ce; k = self.ck; st = self.stages[k]

        # ── STABILIZATION GATE ────────────────────────────────────────────
        # Truoc khi dem rep, yeu cau giu vi tri bat dau (angle > up_angle)
        # on dinh trong STABLE_FRAMES_REQUIRED frame lien tiep.
        # Neu angle roi khoi vung on dinh thi reset bo dem.
        if not self._ready[k]:
            if angle > ex["up_angle"]:
                self._stable_frames[k] += 1
                if self._stable_frames[k] >= self.STABLE_FRAMES_REQUIRED:
                    self._ready[k] = True
                    self.stages[k] = "up"   # Dat stage ve "up" sau khi on dinh
            else:
                self._stable_frames[k] = 0  # Chua on dinh, reset dem
            return False                    # Chua san sang, khong dem rep
        # ── KET THUC GATE — chi chay duoi day khi da san sang ─────────────

        # Vao giai doan "down": bat dau theo doi goc nho nhat va thoi gian
        if angle < ex["down_angle"]:
            if st != "down":
                self.stages[k]            = "down"
                self._rep_start_time[k]   = time.time()
                self._min_angle_in_rep[k] = angle
            else:
                if angle < self._min_angle_in_rep[k]:
                    self._min_angle_in_rep[k] = angle

        # Hoan thanh rep: tu "down" quay ve "up"
        if angle > ex["up_angle"] and st == "down":
            self.stages[k] = "up"
            self.reps[k]  += 1

            # Ghi nhan ROM
            self._rep_roms[k].append(self._min_angle_in_rep[k])

            # Ghi nhan speed
            if self._rep_start_time[k] is not None:
                duration = time.time() - self._rep_start_time[k]
                self._rep_speeds[k].append(duration)

            # Reset cho rep tiep theo
            self._rep_start_time[k]   = None
            self._min_angle_in_rep[k] = 180.0
            return True

        return False

    # ── CAM DATA CHO BRAIN ENGINE ────────────────

    def _calc_speed_score(self) -> float:
        """
        Tinh speed_score (0-100) dua tren thoi gian trung binh moi rep.
        Trong khoang ideal_rep_seconds -> 100 diem.
        Cang lech ra ngoai khoang -> diem cang giam.
        Neu chua co rep nao -> tra ve 50 (trung tinh).
        """
        speeds = self._rep_speeds[self.ck]
        if not speeds:
            return 50.0
        lo, hi = self.ce["ideal_rep_seconds"]
        scores = []
        for dur in speeds:
            if lo <= dur <= hi:
                scores.append(100.0)
            elif dur < lo:
                # Qua nhanh: -10 diem moi 0.5s lech
                deficit = (lo - dur) / 0.5 * 10
                scores.append(max(0.0, 100.0 - deficit))
            else:
                # Qua cham: -8 diem moi 0.5s lech
                deficit = (dur - hi) / 0.5 * 8
                scores.append(max(0.0, 100.0 - deficit))
        return round(sum(scores) / len(scores), 1)

    def _calc_rom_score(self) -> float:
        """
        Tinh rom_score (0-100) dua tren goc nho nhat dat duoc moi rep.
        Dat dung down_angle -> 100. Khong xuong du sau -> tru diem.
        Moi do chenh lech tru 2 diem.
        Neu chua co rep nao -> tra ve 50 (trung tinh).
        """
        roms = self._rep_roms[self.ck]
        if not roms:
            return 50.0
        target = self.ce["down_angle"]
        scores = []
        for min_angle in roms:
            deficit = max(0.0, min_angle - target)  # am = dat du sau, duong = chua du
            scores.append(max(0.0, 100.0 - deficit * 2))
        return round(sum(scores) / len(scores), 1)

    def get_cam_data(self) -> dict:
        """
        Tra ve dict chua du lieu de tao CamData cho brain_engine.
        Goi sau buoi tap de lay ket qua tong hop.

        Ket qua:
            {
                "reps_completed": int,
                "speed_score":    float,  # 0-100
                "rom_score":      float,  # 0-100
            }
        """
        return {
            "reps_completed": self.reps[self.ck],
            "speed_score":    self._calc_speed_score(),
            "rom_score":      self._calc_rom_score(),
        }

    # ── SKELETON & UI ────────────────────────────

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

        # Panel chinh
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

        # Stabilization Gate indicator
        if not self._ready[k]:
            prog  = self._stable_frames[k]
            total = self.STABLE_FRAMES_REQUIRED
            pct   = int((prog / total) * 80)
            # Thanh tien trinh on dinh
            cv2.rectangle(frame, (20, y),    (100, y+12),      (60, 60, 60), -1)
            cv2.rectangle(frame, (20, y),    (20 + pct, y+12), C["orange"],  -1)
            txt(frame, f"On dinh: {prog}/{total}", (20, y+26), 0.48, C["orange"], 1)
            y += 40
        else:
            txt(frame, "READY", (20, y), 0.6, C["green"], 2); y += 24

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

        # Angle bar
        ex = self.ce
        bx, by, bw, bh = 20, H-50, 260, 16
        norm   = np.clip((self.angle - ex["down_angle"]+10) / (ex["up_angle"]-ex["down_angle"]+20), 0, 1)
        filled = int(norm * bw)
        rounded_rect(frame, bx-4, by-4, bw+8, bh+8, 6, C["bg"], 0.7)
        cv2.rectangle(frame, (bx,by), (bx+bw,by+bh), (60,60,60), -1)
        bc = C["green"] if self.stages[k] == "up" else C["orange"]
        cv2.rectangle(frame, (bx,by), (bx+filled,by+bh), bc, -1)
        txt(frame, "Angle range", (bx, by-8), 0.45, C["white"], 1)

        # Huong dan
        if DISPLAY_CONFIG["show_instructions"]:
            for i, t in enumerate(["Q/ESC: Thoat", "N: Tiep  P: Truoc", "R: Reset  Space: Pause"]):
                txt(frame, t, (20, H-95+i*18), 0.42, C["white"], 1)

        # Danh sach bai tap
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

        # Flash xanh khi rep moi
        if flash:
            ov = frame.copy()
            cv2.rectangle(ov, (0,0), (W,H), C["green"], -1)
            cv2.addWeighted(ov, 0.12, frame, 0.88, 0, frame)

    def run(self):
        cap = cv2.VideoCapture (CAMERA_INDEX)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

        if not cap.isOpened():
            print("[LOI] Khong mo duoc camera."); return

        print("\n=")
        print("  AI WORKOUT TRACKER san sang!")
        print("  N / P  -> Doi bai tap")
        print("  R      -> Reset rep")
        print("  Space  -> Pause")
        print("  Q/ESC  -> Thoat")
        print("=\n")

        flash_n = 0

        while True:
            ret, frame = cap.read()
            if not ret: break
            frame = cv2.flip(frame, 1)
            self.update_fps()

            if self.paused:
                self.draw_ui(frame)
                cv2.imshow("AI Workout Tracker", frame)
                key = cv2.waitKey(30) & 0xFF
                if key in (ord('q'), 27): break
                elif key == ord(' '): self.toggle_pause()
                continue

            # Timestamp tang dan (ms) — bat buoc cho VIDEO mode
            timestamp_ms = int(time.time() * 1000)

            # YOLO
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

            # MediaPipe
            lms, wc, hc, off = self.detect_pose(frame, timestamp_ms, best_box)
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

                # Ve goc
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
        print("\n= KET QUA BUOI TAP =")
        print(f"Thoi gian: {fmt_time(self.elapsed())}")
        for k, v in EXERCISES.items():
            if self.reps[k] > 0:
                print(f"  {v['name']}: {self.reps[k]} rep")
        print()
        print("= DU LIEU CHO BRAIN ENGINE (CamData) =")
        cam = self.get_cam_data()
        print(f"  reps_completed : {cam['reps_completed']}")
        print(f"  speed_score    : {cam['speed_score']}  (0=qua nhanh/cham, 100=chuan)")
        print(f"  rom_score      : {cam['rom_score']}  (0=khong du bien do, 100=dat muc tieu)")
        print()

if __name__ == "__main__":
    tracker = WorkoutTracker()
    tracker.run()