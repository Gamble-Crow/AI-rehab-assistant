"""
db.py — SQLite database layer cho PhụcHồi
==========================================
Thay thế hoàn toàn SQL Server + pyodbc.
File rehab.db nằm cạnh exe (hoặc main.py khi dev).

Import:
    from db import init_db, get_db, ...
"""

import sqlite3
import os
import sys
from datetime import date, datetime
from typing import Optional

# ── Đường dẫn DB lấy từ config (data/ cạnh exe khi đóng gói, data/ ở gốc khi dev) ──
from app.config import data
DB_FILE = data("rehab.db")


# ── Connection factory ────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    """Tạo connection mới. row_factory cho phép truy cập bằng tên cột."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Helpers nội bộ ───────────────────────────────────────────────────────────

def _one(sql: str, params: tuple = ()):
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()

def _all(sql: str, params: tuple = ()):
    conn = get_db()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()

def _run(sql: str, params: tuple = ()) -> int:
    """Chạy 1 câu lệnh, trả về lastrowid."""
    conn = get_db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def _transaction(steps: list) -> int:
    """
    Chạy nhiều bước trong 1 transaction.
    steps = [(sql, params), ...]
    Trả về lastrowid của bước cuối.
    """
    conn = get_db()
    try:
        last_id = None
        for sql, params in steps:
            cur = conn.execute(sql, params)
            last_id = cur.lastrowid
        conn.commit()
        return last_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# KHỞI TẠO DATABASE
# ══════════════════════════════════════════════════════════════════════════════

def init_db():
    """
    Tạo tất cả bảng nếu chưa có.
    Chèn data mẫu nếu bảng rỗng.
    An toàn khi gọi nhiều lần.
    """
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS rehab_exercises (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        khop_tap       TEXT    NOT NULL,
        ten            TEXT    NOT NULL,
        huong_dan      TEXT    NOT NULL,
        video_url      TEXT,
        lm_a           INTEGER NOT NULL,   -- landmark MediaPipe diem A
        lm_b           INTEGER NOT NULL,   -- diem B (dinh goc)
        lm_c           INTEGER NOT NULL,   -- diem C
        cam_down_angle INTEGER NOT NULL,   -- nguong goc pha "gap"
        cam_up_angle   INTEGER NOT NULL,   -- nguong goc pha "duoi"
        ideal_sec_min  REAL    NOT NULL,   -- thoi gian/rep ly tuong (min)
        ideal_sec_max  REAL    NOT NULL    -- (max)
    );

    CREATE TABLE IF NOT EXISTS patient (
        patient_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_name  TEXT    NOT NULL,
        date_of_birth TEXT,
        gender        TEXT,
        created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
    );

    CREATE TABLE IF NOT EXISTS current_config (
        config_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id   INTEGER NOT NULL,
        exercise_id  INTEGER NOT NULL,
        current_rep  INTEGER NOT NULL DEFAULT 10,
        updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (patient_id)  REFERENCES patient(patient_id),
        FOREIGN KEY (exercise_id) REFERENCES rehab_exercises(id),
        UNIQUE (patient_id, exercise_id),
        CHECK  (current_rep > 0)
    );

    CREATE TABLE IF NOT EXISTS session_log (
        session_log_id  INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id      INTEGER NOT NULL,
        exercise_id     INTEGER NOT NULL,
        session_date    TEXT    NOT NULL,
        prescribed_rep  INTEGER NOT NULL,
        actual_rep      INTEGER NOT NULL,
        pain_count      INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (patient_id)  REFERENCES patient(patient_id),
        FOREIGN KEY (exercise_id) REFERENCES rehab_exercises(id),
        CHECK (prescribed_rep > 0),
        CHECK (actual_rep     >= 0),
        CHECK (pain_count     >= 0)
    );

    CREATE TABLE IF NOT EXISTS exercise_adjustment (
        adjustment_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        session_log_id    INTEGER NOT NULL,
        patient_id        INTEGER NOT NULL,
        exercise_id       INTEGER NOT NULL,
        adjustment_date   TEXT    NOT NULL,
        current_rep       INTEGER NOT NULL,
        suggested_rep     INTEGER NOT NULL,
        adjustment_action TEXT    CHECK (adjustment_action IN ('tang','giam','giu_nguyen')),
        adjustment_note   TEXT,
        is_confirmed      INTEGER NOT NULL DEFAULT 0,
        confirmed_at      TEXT,
        FOREIGN KEY (session_log_id) REFERENCES session_log(session_log_id),
        FOREIGN KEY (patient_id)     REFERENCES patient(patient_id),
        FOREIGN KEY (exercise_id)    REFERENCES rehab_exercises(id)
    );
    """)
    conn.commit()

    # Dữ liệu mẫu bài tập (ngưỡng góc đã calibrate từ video)
    # (khop_tap,ten,huong_dan,video_url, lm_a,lm_b,lm_c, cam_down,cam_up, sec_min,sec_max)
    if conn.execute("SELECT COUNT(*) FROM rehab_exercises").fetchone()[0] == 0:
        conn.executemany("""
            INSERT INTO rehab_exercises
                (khop_tap,ten,huong_dan,video_url,
                 lm_a,lm_b,lm_c,cam_down_angle,cam_up_angle,ideal_sec_min,ideal_sec_max)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, [
            ("đầu gối","trượt gối",
             "Sử dụng đầu gối để co chân. Nâng đầu gối lên xuống.",
             "01_truot_goi.mp4",
             23,25,27,94,133,2.5,5.0),
            ("đầu gối","nâng chân thẳng",
             "Giữ nguyên chân thẳng, nâng toàn bộ chân lên xuống.",
             "02_nang_chan_thang.mp4",
             11,23,25,142,161,3.0,6.0),
            ("đầu gối","ngồi dựa tường",
             "Đứng dựa lưng vào tường, trượt xuống đến góc đầu gối = 90°.",
             "03_ngoi_dua_tuong.mp4",
             23,25,27,109,145,2.5,5.0),
            ("đầu gối","gập gối đứng",
             "Đứng thẳng, giữ tựa tay vào tường. Gập một đầu gối đưa gót về phía mông.",
             "04_gap_goi_dung.mp4",
             23,25,27,100,140,2.5,5.0),
            ("khuỷu tay","gập/duỗi khuỷu tay",
             "Gập khuỷu tay, tay để trên mặt phẳng.",
             "05_gap_duoi_khuyu_tay.mp4",
             12,14,16,66,129,2.0,4.0),
            ("khuỷu tay","duỗi tay trên đầu",
             "Cánh tay dựng thẳng đứng, gập khuỷu ra sau đầu.",
             "06_duoi_tay_tren_dau.mp4",
             12,14,16,84,130,2.0,4.0),
            ("khuỷu tay","gập cánh tay đứng",
             "Gập khuỷu tay, duỗi hoàn toàn cánh tay.",
             "07_gap_canh_tay_dung.mp4",
             12,14,16,84,127,2.0,4.0),
            ("khuỷu tay","duỗi khuỷu nhờ trọng lực",
             "Giữ nguyên cánh tay lơ lửng trên không.",
             "08_duoi_khuyu_nho_trong_luc.mp4",
             12,14,16,56,122,2.0,4.0),
        ])
        conn.commit()

    # Bệnh nhân mẫu
    if conn.execute("SELECT COUNT(*) FROM patient").fetchone()[0] == 0:
        conn.execute("""
            INSERT INTO patient (patient_name, date_of_birth, gender)
            VALUES ('Nguyễn Văn An','2000-05-15','Nam')
        """)
        conn.commit()

    conn.close()
    print(f"[DB] Sẵn sàng: {DB_FILE}")


# ══════════════════════════════════════════════════════════════════════════════
# PYTHON FUNCTIONS thay thế Stored Procedures
# ══════════════════════════════════════════════════════════════════════════════

def get_patients() -> list[dict]:
    rows = _all("SELECT patient_id, patient_name, date_of_birth, gender FROM patient ORDER BY patient_name")
    return [dict(r) for r in rows]

def add_patient(name: str, dob: Optional[str] = None, gender: Optional[str] = None) -> int:
    return _run(
        "INSERT INTO patient (patient_name, date_of_birth, gender) VALUES (?,?,?)",
        (name, dob, gender)
    )

def get_exercises() -> list[dict]:
    rows = _all("""
        SELECT id, khop_tap, ten, huong_dan, video_url,
               lm_a, lm_b, lm_c, cam_down_angle, cam_up_angle,
               ideal_sec_min, ideal_sec_max
        FROM rehab_exercises ORDER BY khop_tap, id
    """)
    return [dict(r) for r in rows]

def get_exercise(exercise_id: int) -> Optional[dict]:
    row = _one("SELECT * FROM rehab_exercises WHERE id=?", (exercise_id,))
    return dict(row) if row else None

# Tương đương sp_get_current_rep
def get_current_rep(patient_id: int, exercise_id: int) -> int:
    row = _one("""
        SELECT current_rep FROM current_config
        WHERE patient_id=? AND exercise_id=?
    """, (patient_id, exercise_id))
    return row["current_rep"] if row else 10

# Tương đương sp_save_session
def save_session(
    patient_id:       int,
    exercise_id:      int,
    prescribed_rep:   int,
    actual_rep:       int,
    pain_count:       int,
    suggested_rep:    int,
    adjustment_action:str,
    adjustment_note:  str,
) -> int:
    """
    Ghi session_log + exercise_adjustment trong 1 transaction.
    Trả về session_log_id mới.
    """
    current_rep = get_current_rep(patient_id, exercise_id)
    today = str(date.today())

    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO session_log
                (patient_id, exercise_id, session_date,
                 prescribed_rep, actual_rep, pain_count)
            VALUES (?,?,?,?,?,?)
        """, (patient_id, exercise_id, today,
              prescribed_rep, actual_rep, pain_count))
        session_log_id = cur.lastrowid

        conn.execute("""
            INSERT INTO exercise_adjustment
                (session_log_id, patient_id, exercise_id,
                 adjustment_date, current_rep, suggested_rep,
                 adjustment_action, adjustment_note)
            VALUES (?,?,?,?,?,?,?,?)
        """, (session_log_id, patient_id, exercise_id, today,
              current_rep, suggested_rep,
              adjustment_action, adjustment_note))

        conn.commit()
        return session_log_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def confirm_session(session_log_id: int, patient_id: int, exercise_id: int, new_rep: int):
    """Đánh dấu adjustment là confirmed + cập nhật current_config."""
    _transaction([
        ("""
            UPDATE exercise_adjustment
            SET is_confirmed=1, confirmed_at=datetime('now','localtime')
            WHERE session_log_id=? AND patient_id=? AND exercise_id=?
         """, (session_log_id, patient_id, exercise_id)),
        ("""
            INSERT INTO current_config (patient_id, exercise_id, current_rep, updated_at)
            VALUES (?,?,?,datetime('now','localtime'))
            ON CONFLICT(patient_id, exercise_id)
            DO UPDATE SET current_rep=excluded.current_rep, updated_at=excluded.updated_at
         """, (patient_id, exercise_id, new_rep)),
    ])

def get_history(patient_id: int, limit: int = 10) -> list[dict]:
    rows = _all("""
        SELECT
            sl.session_log_id,
            sl.session_date,
            re.ten          AS exercise_name,
            sl.prescribed_rep,
            sl.actual_rep,
            sl.pain_count,
            ea.adjustment_action,
            ea.suggested_rep,
            ea.is_confirmed
        FROM session_log sl
        JOIN rehab_exercises re ON re.id = sl.exercise_id
        LEFT JOIN exercise_adjustment ea
            ON ea.session_log_id = sl.session_log_id
        WHERE sl.patient_id = ?
        ORDER BY sl.session_date DESC, sl.session_log_id DESC
        LIMIT ?
    """, (patient_id, limit))
    return [dict(r) for r in rows]