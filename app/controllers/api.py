import threading
import time
import os
import sys
import json

import webview   # pip install pywebview

from app.config import view, data
from app.models.database import init_db, get_patients, get_exercises, get_exercise, \
               get_current_rep, get_session_rep_info, save_session, confirm_session, get_history
from app.models.brain_engine import BrainEngine, SessionInput, CamData, MicData, JointType
from app.services.camera import WorkoutTracker, start_mjpeg_server, MJPEG_PORT

# SESSION STATE
class _Session:
    def reset(self):
        self.patient_id:     int   = 0
        self.patient_name:   str   = ""
        self.exercise_id:    int   = 0
        self.exercise_name:  str   = ""
        self.prescribed_rep: int   = 10
        self.joint:          JointType = JointType.KNEE
        self.start_time:     float = 0.0
        self.tracker:        WorkoutTracker | None = None
        self.cam_thread:     threading.Thread | None = None
        self.pain_counter                = None
        self.pain_recognizer             = None
        self.last_rec                    = None   # Recommendation
        self.session_log_id: int         = 0

    def __init__(self): self.reset()

SESSION = _Session()
BRAIN   = BrainEngine(state_file=data("brain_states.json"))

# API — mỗi method public được JS gọi qua window.pywebview.api
class Api:
    """
    Tất cả method trả về dict có dạng:
        {"ok": True/False, "data": ..., "error": "..."}
    JS nhận về và xử lý.
    """

    # 1. Lấy danh sách bệnh nhân khi load trang
    def get_patients(self) -> dict:
        try:
            return {"ok": True, "data": get_patients()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 1b. Tạo bệnh nhân mới + đăng nhập (JS gọi khi nhập tên mới)
    def add_patient_and_login(self, patient_name: str) -> dict:
        try:
            from app.models.database import add_patient
            new_id = add_patient(str(patient_name))
            SESSION.reset()
            SESSION.patient_id   = new_id
            SESSION.patient_name = str(patient_name)
            exercises = get_exercises()
            return {"ok": True, "data": {
                "patient_id":   new_id,
                "patient_name": str(patient_name),
                "exercises":    exercises,
            }}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 2. Đăng nhập
    def login(self, patient_id: int, patient_name: str) -> dict:
        try:
            SESSION.reset()
            SESSION.patient_id   = int(patient_id)
            SESSION.patient_name = str(patient_name)
            exercises = get_exercises()
            return {"ok": True, "data": {"exercises": exercises}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 3. Lấy cường độ hiện tại khi chọn bài tập
    def get_current_rep(self, exercise_id: int) -> dict:
        try:
            info = get_session_rep_info(SESSION.patient_id, int(exercise_id))
            return {"ok": True, "data": info}   # {current_rep, is_first}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 4. Bắt đầu buổi tập
    def start_session(self, exercise_id: int, prescribed_rep: int) -> dict:
        """
        Đọc cấu hình bài tập từ DB, khởi động camera (MJPEG) + mic.
        Sau mỗi rep, gọi evaluate_js('updateRepCount(...)') về UI.
        """
        try:
            ex = get_exercise(int(exercise_id))
            if not ex:
                return {"ok": False, "error": "Không tìm thấy bài tập"}

            SESSION.exercise_id    = int(exercise_id)
            SESSION.exercise_name  = ex["ten"]
            SESSION.prescribed_rep = int(prescribed_rep)
            SESSION.start_time     = time.time()
            khop = (ex["khop_tap"] or "").lower()
            SESSION.joint = JointType.ELBOW if "khuỷu" in khop or "khuyu" in khop \
                            else JointType.KNEE

            # Cấu hình engine lấy thẳng từ DB (không còn hardcode / KEY_MAP)
            config = {
                "name":              ex["ten"],
                "joints":            (ex["lm_a"], ex["lm_b"], ex["lm_c"]),
                "down_angle":        ex["cam_down_angle"],
                "up_angle":          ex["cam_up_angle"],
                "ideal_rep_seconds": (ex["ideal_sec_min"], ex["ideal_sec_max"]),
            }
            tracker = WorkoutTracker(config)
            SESSION.tracker = tracker

            # Báo UI khi camera thật sự mở (khung hình đầu tiên) → mới bắt đầu đếm giờ
            def on_ready():
                try: _window.evaluate_js("onCameraReady()")
                except Exception: pass
            tracker.on_ready = on_ready

            # Callback: mỗi rep → evaluate_js về UI
            def on_rep(rep_cur: int, total: int):
                pain = SESSION.pain_counter.pain_count \
                       if SESSION.pain_counter else 0
                js = (f"updateRepCount("
                      f"{rep_cur}, {total}, {pain})")
                try:
                    _window.evaluate_js(js)
                except Exception:
                    pass

            tracker.on_rep = on_rep

            # Callback throttle: đẩy số liệu sống (rep/pha/góc/đau) lên cột thông tin
            def on_stats(rep_cur, total, phase, angle, ready):
                pain = SESSION.pain_counter.pain_count if SESSION.pain_counter else 0
                js = (f"updateStats({rep_cur}, {total}, "
                      f"{json.dumps(phase, ensure_ascii=False)}, {angle}, {pain})")
                try:
                    _window.evaluate_js(js)
                except Exception:
                    pass
            tracker.on_stats = on_stats

            # Chạy camera trong thread riêng
            def cam_loop():
                tracker.run()

            SESSION.cam_thread = threading.Thread(target=cam_loop, daemon=True)
            SESSION.cam_thread.start()

            # Khởi động mic
            self._start_mic()

            return {
                "ok": True,
                "data": {
                    "mjpeg_url":    f"http://127.0.0.1:{MJPEG_PORT}/video",
                    "prescribed_rep": prescribed_rep,
                }
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _start_mic(self):
        def _loop():
            try:
                from app.services.pain_detector import PainCryCounter, SpeechRecognizer
                SESSION.pain_counter    = PainCryCounter()
                SESSION.pain_recognizer = SpeechRecognizer()
                def _on_mic(t, r, cry=0.0, label="", speech=0.0):
                    is_pain = SESSION.pain_counter.record(t, r, cry)
                    if t.strip() or cry > 0.1:
                        print(f"[MIC] text='{t}' rms={r:.3f} cry={cry:.2f}({label}) "
                              f"speech={speech:.2f} dau={is_pain} tong={SESSION.pain_counter.pain_count}")
                SESSION.pain_recognizer.start(callback=_on_mic)
            except Exception as ex:
                print(f"[MIC] Lỗi: {ex}")
        threading.Thread(target=_loop, daemon=True).start()

    # 5. Tạm dừng / tiếp tục
    def toggle_pause(self) -> dict:
        try:
            if SESSION.tracker:
                SESSION.tracker.toggle_pause()
                return {"ok": True, "data": {"paused": SESSION.tracker.paused}}
            return {"ok": False, "error": "Chưa có buổi tập"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 6. Kết thúc buổi tập
    def end_session(self) -> dict:
        """
        Dừng camera + mic → gọi BrainEngine → gọi evaluate_js('showResult(...)')
        """
        try:
            # Dừng camera
            cam_data = {"reps_completed": 0, "speed_score": 50.0, "rom_score": 50.0}
            if SESSION.tracker:
                SESSION.tracker.stop()
                if SESSION.cam_thread:
                    SESSION.cam_thread.join(timeout=10.0)
                cam_data = SESSION.tracker.get_cam_data()

            # Dừng mic
            pain_count = 0
            if SESSION.pain_recognizer:
                SESSION.pain_recognizer.stop()
                time.sleep(0.3)
            if SESSION.pain_counter:
                pain_count = SESSION.pain_counter.pain_count

            elapsed = int(SESSION.tracker.elapsed()) if SESSION.tracker \
                      else int(time.time() - SESSION.start_time)
            duration = f"{elapsed//60:02d}:{elapsed%60:02d}"

            actual = cam_data["reps_completed"]

            # Ca 2.1: 0 rep → bỏ hẳn, không lưu, không gọi brain, yêu cầu tập lại
            if actual == 0:
                SESSION.last_rec = None
                retry = {
                    "direction":      "retry",
                    "reps_completed": 0,
                    "prescribed_rep": SESSION.prescribed_rep,
                    "pain_count":     pain_count,
                    "duration":       duration,
                    "exercise_name":  SESSION.exercise_name,
                }
                try:
                    _window.evaluate_js(f"showResult({json.dumps(retry, ensure_ascii=False)})")
                except Exception:
                    pass
                return {"ok": True, "data": retry}

            # Ca 2.2: BUỔI 1 (chưa có session_log cho bài này) + tập thiếu số đã chọn
            # → hạ mục tiêu = số rep đã tập, lưu actual/actual làm dữ liệu nền.
            # Phần điều chỉnh còn lại do brain engine xử lý NHƯ CŨ (không sửa brain_engine.py).
            if actual < SESSION.prescribed_rep:
                if get_session_rep_info(SESSION.patient_id, SESSION.exercise_id)["is_first"]:
                    SESSION.prescribed_rep = actual

            form_score = round((cam_data["speed_score"] + cam_data["rom_score"]) / 2, 1)

            # Tính week_number
            hist = get_history(SESSION.patient_id, limit=100)
            week_number = max(1, (len(hist) // 3) + 1)

            # Gọi Brain Engine
            rec = BRAIN.analyze(SessionInput(
                patient_id   = str(SESSION.patient_id),
                exercise_id  = str(SESSION.exercise_id),
                joint        = SESSION.joint,
                reps_target  = SESSION.prescribed_rep,
                cam          = CamData(
                    reps_completed = cam_data["reps_completed"],
                    speed_score    = cam_data["speed_score"],
                    rom_score      = cam_data["rom_score"],
                    form_score     = form_score,
                ),
                mic          = MicData(pain_events=pain_count),
                week_number  = week_number,
            ))
            SESSION.last_rec = rec

            result = {
                "reps_completed":   cam_data["reps_completed"],
                "prescribed_rep":   SESSION.prescribed_rep,
                "pain_count":       pain_count,
                "duration":         duration,
                "form_score":       form_score,
                "direction":        rec.direction.value,
                "current_reps":     rec.current_reps,
                "suggested_reps":   rec.suggested_reps,
                "composite_score":  rec.composite_score,
                "reasons":          rec.reasons,
                "stability_note":   rec.stability_note,
                "is_safety_override": rec.is_safety_override,
                "patient_id":       SESSION.patient_id,
                "exercise_id":      SESSION.exercise_id,
                "exercise_name":    SESSION.exercise_name,
            }

            # Gọi showResult() trong app.html
            js = f"showResult({json.dumps(result, ensure_ascii=False)})"
            try:
                _window.evaluate_js(js)
            except Exception:
                pass

            return {"ok": True, "data": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 7. Lưu kết quả (đồng ý / từ chối đề xuất)
    def save_result(self, agreed: bool, data: dict) -> dict:
        """
        JS gọi: window.pywebview.api.save_result(true/false, pendingResult)
        Lưu SQLite + cập nhật Brain Engine state.
        """
        try:
            rec        = SESSION.last_rec
            agreed     = bool(agreed)
            final_reps = data.get("suggested_reps", 10) if agreed \
                         else data.get("current_reps", 10)

            if not agreed:
                action, note = "giu_nguyen", "Bệnh nhân từ chối đề xuất."
            elif final_reps > data.get("current_reps", 10):
                action, note = "tang", f"Tăng → {final_reps} reps"
            elif final_reps < data.get("current_reps", 10):
                action, note = "giam", f"Giảm → {final_reps} reps"
            else:
                action, note = "giu_nguyen", "Giữ nguyên."

            reasons_str = "; ".join(rec.reasons) if rec else ""
            full_note   = note + (" | " + reasons_str if reasons_str else "")

            # Lưu session_log + exercise_adjustment
            sid = save_session(
                patient_id        = data["patient_id"],
                exercise_id       = data["exercise_id"],
                prescribed_rep    = SESSION.prescribed_rep,
                actual_rep        = data.get("reps_completed", 0),
                pain_count        = data.get("pain_count", 0),
                suggested_rep     = final_reps,
                adjustment_action = action,
                adjustment_note   = full_note,
            )
            SESSION.session_log_id = sid

            # Nếu đồng ý → cập nhật current_config
            if agreed:
                confirm_session(sid, data["patient_id"], data["exercise_id"], final_reps)
                BRAIN.confirm_recommendation(
                    str(data["patient_id"]), str(data["exercise_id"]), SESSION.prescribed_rep
                )
            else:
                BRAIN.reject_recommendation(
                    str(data["patient_id"]), str(data["exercise_id"]), SESSION.prescribed_rep
                )

            SESSION.last_rec = None
            return {"ok": True, "data": {"action": action, "final_reps": final_reps}}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # 8. Lịch sử buổi tập
    def get_history(self, limit: int = 10) -> dict:
        try:
            return {"ok": True, "data": get_history(SESSION.patient_id, limit)}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ENTRY POINT
_window: webview.Window = None

def run():
    global _window

    # Khởi tạo DB
    init_db()

    # Khởi động MJPEG server ngầm
    start_mjpeg_server(MJPEG_PORT)

    # Tạo API instance
    api = Api()

    # Tạo cửa sổ PyWebView duy nhất
    _window = webview.create_window(
        title       = "PhụcHồi",
        url         = view("app.html"),
        js_api      = api,
        width       = 1280,
        height      = 800,
        min_size    = (1024, 600),
        resizable   = True,
    )

    # Chạy — blocking cho đến khi đóng cửa sổ
    webview.start(debug=False)