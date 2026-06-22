"""
Bộ Não - Rehabilitation Brain Engine
=====================================
Module xử lý dữ liệu từ cam (AI pose tracking) và mic (pain detection)
để gợi ý điều chỉnh cường độ tập phục hồi chức năng.

Đầu vào:
  - CamData: số reps, tốc độ, biên độ khớp (ROM)
  - MicData: số lần phát hiện tiếng kêu đau

Đầu ra:
  - Recommendation: hướng điều chỉnh + số reps mới + lý do
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json
import os
from datetime import datetime


# ─────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────

class JointType(str, Enum):
    KNEE = "knee"
    ELBOW = "elbow"

class Direction(str, Enum):
    INCREASE = "increase"
    MAINTAIN = "maintain"
    REDUCE   = "reduce"


@dataclass
class CamData:
    """
    Dữ liệu từ module camera/AI pose tracking.
    Tất cả giá trị score trong khoảng [0, 100].
    """
    reps_completed:  int    # Số reps bệnh nhân thực sự hoàn thành
    speed_score:     float  # Chất lượng tốc độ: 0=rất chậm/run, 100=chuẩn
    rom_score:       float  # Range of motion đạt được: 0=rất kém, 100=đầy đủ


@dataclass
class MicData:
    """
    Dữ liệu từ module microphone/pain detection.
    """
    pain_events: int  # Số lần phát hiện tiếng kêu đau trong buổi


@dataclass
class SessionInput:
    """
    Toàn bộ dữ liệu một buổi tập, truyền vào BrainEngine.analyze().
    """
    patient_id:    str
    exercise_id:   str        # Ví dụ: "knee_flex", "elbow_eccentric"
    joint:         JointType
    reps_target:   int        # Số reps bệnh nhân được chỉ định
    cam:           CamData
    mic:           MicData
    week_number:   int        # Tuần phục hồi (1–24)
    session_date:  str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class Recommendation:
    """
    Kết quả gợi ý từ BrainEngine.
    """
    direction:          Direction
    suggested_reps:     int
    current_reps:       int
    composite_score:    float          # 0–100
    factor_scores:      dict           # Chi tiết từng yếu tố
    reasons:            list[str]
    stability_note:     str            # Giải thích về cơ chế chống dao động
    is_safety_override: bool           # True nếu bị override vì an toàn
    confirmed:          bool           # True nếu đã qua cơ chế xác nhận 2 buổi


# ─────────────────────────────────────────────
# STABILITY STATE (per patient, per exercise)
# ─────────────────────────────────────────────

@dataclass
class StabilityState:
    """
    Trạng thái theo dõi xu hướng để chống dao động.
    Lưu theo cặp (patient_id, exercise_id).

    Cơ chế chống dao động:
      - CONFIRM_THRESHOLD = 1: chỉ cần 1 buổi tốt để gợi ý tăng.
      - Trước khi tăng, OscillationDetector kiểm tra reps_history
        (OSCILLATION_WINDOW buổi gần nhất) — nếu giá trị đề xuất đã
        từng xuất hiện rồi bị giảm xuống thì giữ nguyên, tránh 7→8→7→8.
    """
    # current_reps DA BO: nguon duy nhat ve "reps hien tai" la DB (current_config).
    # Brain chi giu trang thai chong dao dong (pending + lich su reps DA TAP).
    pending_dir:        Optional[Direction] = None
    pending_count:      int = 0
    reps_history:       list = field(default_factory=list)  # Lịch sử reps ĐÃ TẬP theo buổi
    CONFIRM_THRESHOLD:  int = 1         # Cần N buổi liên tiếp để xác nhận thay đổi


# ─────────────────────────────────────────────
# SCORING ENGINE
# ─────────────────────────────────────────────

class ScoringEngine:
    """
    Tính điểm tổng hợp từ dữ liệu cam + mic.
    Điểm cao = bệnh nhân tập tốt → có thể tăng.
    Điểm thấp = đang gặp khó khăn → cần giảm.
    """

    # Trọng số các yếu tố
    WEIGHTS = {
        "pain":       0.35,  # Tín hiệu đau — ưu tiên cao nhất
        "speed":      0.25,  # Tốc độ thực hiện
        "rom":        0.20,  # Biên độ khớp
        "completion": 0.20,  # Hoàn thành reps mục tiêu
    }

    # Ngưỡng quyết định hướng
    THRESHOLD_INCREASE = 80   # Điểm >= 80 → gợi ý tăng
    THRESHOLD_MAINTAIN = 50   # Điểm 50–79 → duy trì
                              # Điểm < 50  → gợi ý giảm

    def compute(self, inp: SessionInput) -> tuple[float, dict, list[str]]:
        """
        Trả về: (composite_score, factor_scores, reasons)
        """
        reasons = []
        factors = {}

        # --- Factor 1: Pain (from mic) ---
        p = inp.mic.pain_events
        if p == 0:
            factors["pain"] = 100.0
            reasons.append("không có tín hiệu đau")
        elif p <= 2:
            factors["pain"] = 55.0
            reasons.append(f"đau nhẹ ({p} lần)")
        elif p <= 5:
            factors["pain"] = 25.0
            reasons.append(f"đau trung bình ({p} lần)")
        else:
            factors["pain"] = 0.0
            reasons.append(f"đau nhiều ({p} lần)")

        # --- Factor 2: Speed (from cam) ---
        factors["speed"] = float(inp.cam.speed_score)
        if inp.cam.speed_score < 40:
            reasons.append("tốc độ thực hiện chậm/không đều")
        elif inp.cam.speed_score > 80:
            reasons.append("tốc độ thực hiện tốt")

        # --- Factor 3: ROM (from cam) ---
        factors["rom"] = float(inp.cam.rom_score)
        if inp.cam.rom_score < 40:
            reasons.append("biên độ khớp chưa đạt")
        elif inp.cam.rom_score > 80:
            reasons.append("biên độ khớp đạt tốt")

        # --- Factor 4: Completion ratio ---
        ratio = inp.cam.reps_completed / max(inp.reps_target, 1)
        if ratio >= 1.0:
            factors["completion"] = 100.0
            reasons.append("hoàn thành đủ/vượt số reps")
        elif ratio >= 0.8:
            factors["completion"] = 70.0
        elif ratio >= 0.6:
            factors["completion"] = 40.0
            reasons.append("hoàn thành dưới 80% reps mục tiêu")
        else:
            factors["completion"] = 15.0
            reasons.append("không hoàn thành reps mục tiêu")

        # --- Weighted composite ---
        composite = sum(factors[k] * self.WEIGHTS[k] for k in self.WEIGHTS)

        # Bonus/penalty theo tuần phục hồi
        if inp.week_number <= 4:
            composite -= 10   # Giai đoạn đầu: cẩn thận hơn
        elif inp.week_number >= 16:
            composite += 5    # Giai đoạn cuối: có thể mạnh dạn hơn

        composite = max(0.0, min(100.0, composite))
        return composite, factors, reasons

    def raw_direction(self, score: float, pain_events: int) -> Direction:
        """
        Quyết định hướng thô dựa trên điểm và override an toàn.
        """
        # Safety override: đau nhiều → luôn giảm
        if pain_events >= 6:
            return Direction.REDUCE

        if score >= self.THRESHOLD_INCREASE:
            raw = Direction.INCREASE
        elif score >= self.THRESHOLD_MAINTAIN:
            raw = Direction.MAINTAIN
        else:
            raw = Direction.REDUCE

        # Safety override: đau trung bình → không tăng
        if pain_events >= 3 and raw == Direction.INCREASE:
            return Direction.MAINTAIN

        return raw


# ─────────────────────────────────────────────
# REP ADJUSTMENT RULES
# ─────────────────────────────────────────────

class RepAdjuster:
    """
    Tính số reps mới dựa trên hướng điều chỉnh.
    Tăng nhẹ (1 rep), giảm đáng kể (~40%) để bệnh nhân cảm nhận được.
    """
    MAX_REPS = 20
    MIN_REPS = 1
    INCREASE_STEP = 1         # Tăng từng bước nhỏ
    REDUCE_RATIO  = 0.6       # Giảm xuống còn 60% (giảm 40%)

    def adjust(self, current_reps: int, direction: Direction) -> int:
        if direction == Direction.INCREASE:
            return min(current_reps + self.INCREASE_STEP, self.MAX_REPS)
        elif direction == Direction.REDUCE:
            return max(round(current_reps * self.REDUCE_RATIO), self.MIN_REPS)
        else:
            return current_reps


# ─────────────────────────────────────────────
# OSCILLATION DETECTOR
# ─────────────────────────────────────────────

class OscillationDetector:
    """
    Kiểm tra lịch sử reps để phát hiện dao động trước khi cho phép tăng.

    Ví dụ cần chặn: lịch sử [7, 8, 7] → đề xuất tăng lên 8 → CHẶN
    Vì bệnh nhân đã từng ở mức 8 nhưng sau đó bị giảm xuống,
    chứng tỏ mức đó chưa ổn định.

    OSCILLATION_WINDOW = 4: kiểm tra 4 buổi gần nhất.
      - Đủ rộng để bắt 7→8→7 và 7→8→7→7.
      - Không quá bảo thủ như window 6+, tránh cản trở tiến bộ thật sự.

      Trả về (oscillating: bool, note: str).

        Điều kiện chặn: trong OSCILLATION_WINDOW buổi gần nhất,
        tìm thấy ít nhất 1 lần giá trị == proposed_reps
        và buổi ngay sau đó có reps thấp hơn (tức đã bị giảm xuống).
    """

    OSCILLATION_WINDOW = 4

    def is_oscillating(self, reps_history: list, proposed_reps: int) -> tuple[bool, str]:
        recent = reps_history[-self.OSCILLATION_WINDOW:]
        if len(recent) < 2:
            return False, ""

        for i in range(len(recent) - 1):
            if recent[i] == proposed_reps and recent[i + 1] < proposed_reps:
                return (
                    True,
                    f"Phát hiện dao động trong {self.OSCILLATION_WINDOW} buổi gần nhất "
                    f"(lịch sử reps: {recent}): mức {proposed_reps} reps đã từng bị "
                    f"giảm xuống → giữ nguyên để ổn định trước khi thử lại."
                )

        return False, ""


# ─────────────────────────────────────────────
# STABILITY FILTER (chống dao động)
# ─────────────────────────────────────────────

class StabilityFilter:
    """
    Ngăn bộ não thay đổi gợi ý liên tục (7→8→7→8...).

    Cơ chế mới (2 lớp bảo vệ):
      1. Xác nhận nhanh (1 buổi): chỉ cần 1 buổi liên tiếp cùng hướng
         để áp dụng thay đổi — phản ứng nhanh hơn với tình trạng thực tế.
      2. Kiểm tra dao động (OscillationDetector): trước khi cho phép tăng,
         quét 4 buổi gần nhất — nếu mức đề xuất đã bị giảm trước đó thì block.
    """

    def __init__(self):
        self._osc = OscillationDetector()

    def apply(
        self,
        state: StabilityState,
        raw_dir: Direction,
        proposed_reps: int,
    ) -> tuple[Direction, bool, str]:
        """
        Trả về: (final_direction, confirmed, stability_note)

        Tham số proposed_reps: số reps dự kiến nếu áp dụng raw_dir,
        dùng để kiểm tra oscillation trước khi xác nhận tăng.
        """
        if raw_dir == Direction.MAINTAIN:
            # Maintain → reset pending tăng để tránh tích lũy sai
            if state.pending_dir == Direction.INCREASE:
                state.pending_dir = None
                state.pending_count = 0
            return Direction.MAINTAIN, True, ""

        # Cùng hướng với buổi trước → tăng đếm; khác hướng → reset
        if state.pending_dir == raw_dir:
            state.pending_count += 1
        else:
            state.pending_dir = raw_dir
            state.pending_count = 1

        if state.pending_count >= state.CONFIRM_THRESHOLD:
            # Đủ điều kiện xác nhận về mặt đếm buổi

            if raw_dir == Direction.INCREASE:
                # Lớp bảo vệ 2: kiểm tra oscillation trước khi cho tăng
                osc, osc_note = self._osc.is_oscillating(state.reps_history, proposed_reps)
                if osc:
                    # Block tăng, nhưng không reset pending —
                    # cần thêm buổi ổn định liên tiếp để vượt qua cửa oscillation
                    state.pending_count = 0
                    return Direction.MAINTAIN, False, osc_note

            state.pending_count = 0
            note = f"Xác nhận sau {state.CONFIRM_THRESHOLD} buổi liên tiếp — áp dụng thay đổi."
            return raw_dir, True, note
        else:
            remaining = state.CONFIRM_THRESHOLD - state.pending_count
            dir_label = "tốt" if raw_dir == Direction.INCREASE else "cần giảm"
            note = (
                f"Bộ não đang quan sát (buổi {state.pending_count}/{state.CONFIRM_THRESHOLD}). "
                f"Cần thêm {remaining} buổi {dir_label} nữa để xác nhận. "
                f"Tạm thời duy trì cường độ hiện tại."
            )
            return Direction.MAINTAIN, False, note


# ─────────────────────────────────────────────
# BRAIN ENGINE (main class)
# ─────────────────────────────────────────────

class BrainEngine:
    """
    Bộ não chính — nhận SessionInput, trả về Recommendation.

    Sử dụng:
        engine = BrainEngine()
        rec = engine.analyze(session_input)
        print(rec.suggested_reps, rec.direction, rec.reasons)
    """

    def __init__(self, state_file: Optional[str] = None):
        """
        state_file: đường dẫn file JSON để lưu/load stability states.
                    Nếu None thì chỉ lưu in-memory (mất khi tắt app).
        """
        self._scorer   = ScoringEngine()
        self._adjuster = RepAdjuster()
        self._filter   = StabilityFilter()
        self._state_file = state_file

        # key = f"{patient_id}::{exercise_id}"
        self._states: dict[str, StabilityState] = {}

        if state_file and os.path.exists(state_file):
            self._load_states()

    # ── PUBLIC API ──────────────────────────────

    def analyze(self, inp: SessionInput) -> Recommendation:
        """
        Phân tích buổi tập và trả về gợi ý điều chỉnh.
        """
        key = f"{inp.patient_id}::{inp.exercise_id}"

        # Lấy hoặc khởi tạo stability state
        if key not in self._states:
            self._states[key] = StabilityState()
        state = self._states[key]

        # NGUON DUY NHAT cho "reps hien tai" = reps_target (DB/UI), khong luu trong brain
        base_reps = inp.reps_target

        # Bước 1: Tính điểm
        score, factors, reasons = self._scorer.compute(inp)

        # Bước 2: Quyết định hướng thô
        raw_dir = self._scorer.raw_direction(score, inp.mic.pain_events)
        is_safety = (inp.mic.pain_events >= 6) or (
            inp.mic.pain_events >= 3 and raw_dir == Direction.MAINTAIN
            and self._scorer.raw_direction(score, 0) == Direction.INCREASE
        )

        # Bước 2.5: Tính trước proposed_reps để truyền cho StabilityFilter
        # (cần biết mức đề xuất để kiểm tra oscillation)
        proposed_reps = self._adjuster.adjust(base_reps, raw_dir)

        # Bước 3: Lọc qua stability filter
        # Đau nhiều (>=6) → bypass filter, giảm ngay để đảm bảo an toàn
        if inp.mic.pain_events >= 6:
            final_dir, confirmed, stab_note = (
                Direction.REDUCE, True,
                "Override an toàn: giảm ngay do phát hiện đau nhiều."
            )
        else:
            final_dir, confirmed, stab_note = self._filter.apply(
                state, raw_dir, proposed_reps
            )

        # Bước 4: Tính số reps mới — chỉ là gợi ý, chưa áp dụng.
        # reps_history KHÔNG ghi ở đây — chỉ ghi khi buổi tập được LƯU
        # (confirm/reject), để buổi chưa lưu không lọt vào kiểm tra dao động.
        new_reps = proposed_reps if final_dir != Direction.MAINTAIN else base_reps

        if self._state_file:
            self._save_states()

        return Recommendation(
            direction         = final_dir,
            suggested_reps    = new_reps,
            current_reps      = base_reps,
            composite_score   = round(score, 1),
            factor_scores     = {k: round(v, 1) for k, v in factors.items()},
            reasons           = reasons,
            stability_note    = stab_note,
            is_safety_override= is_safety,
            confirmed         = confirmed,
        )

    def get_state(self, patient_id: str, exercise_id: str) -> Optional[StabilityState]:
        return self._states.get(f"{patient_id}::{exercise_id}")

    def _finalize(self, patient_id: str, exercise_id: str, trained_reps: int):
        """Ghi nhận buổi tập đã LƯU: thêm reps đã tập vào lịch sử + reset pending."""
        key = f"{patient_id}::{exercise_id}"
        if key not in self._states:
            self._states[key] = StabilityState()
        st = self._states[key]
        st.reps_history.append(int(trained_reps))
        st.reps_history = st.reps_history[-10:]   # giữ tối đa 10 buổi gần nhất
        st.pending_dir = None
        st.pending_count = 0
        if self._state_file:
            self._save_states()

    def confirm_recommendation(self, patient_id: str, exercise_id: str, trained_reps: int):
        """
        Bệnh nhân đồng ý → ghi nhận buổi đã lưu.
        (current_config trong DB do db.py cập nhật; brain KHÔNG giữ current_reps nữa.)
        trained_reps = số reps bệnh nhân ĐÃ TẬP buổi này (= prescribed_rep).
        """
        self._finalize(patient_id, exercise_id, trained_reps)

    def reject_recommendation(self, patient_id: str, exercise_id: str, trained_reps: int):
        """Bệnh nhân từ chối → vẫn ghi nhận buổi đã tập + reset pending."""
        self._finalize(patient_id, exercise_id, trained_reps)

    def reset_state(self, patient_id: str, exercise_id: str):
        """Reset state khi bắt đầu bài tập mới hoặc thay đổi giai đoạn."""
        key = f"{patient_id}::{exercise_id}"
        if key in self._states:
            del self._states[key]
        if self._state_file:
            self._save_states()

    # ── PERSISTENCE ──────────────────────────────

    def _save_states(self):
        data = {
            k: {
                "pending_dir":   s.pending_dir.value if s.pending_dir else None,
                "pending_count": s.pending_count,
                "reps_history":  s.reps_history,
            }
            for k, s in self._states.items()
        }
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _load_states(self):
        with open(self._state_file, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            self._states[k] = StabilityState(
                pending_dir   = Direction(v["pending_dir"]) if v.get("pending_dir") else None,
                pending_count = v.get("pending_count", 0),
                reps_history  = v.get("reps_history", []),
            )