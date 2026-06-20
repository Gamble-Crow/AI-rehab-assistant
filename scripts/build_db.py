"""
build_db.py — tao file DB MOI (rehab_new.db) de xem truoc khi chuyen.
KHONG dung toi rehab.db cu.

Schema rehab_exercises moi: bo cac cot text chet (up_angle/down_angle/diem_a/b/c),
them cac cot "engine" de camera.py doc truc tiep + video_url.
"""
import sqlite3, os, sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rehab_new.db")
if os.path.exists(OUT):
    os.remove(OUT)

conn = sqlite3.connect(OUT)
conn.executescript("""
CREATE TABLE rehab_exercises (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    khop_tap       TEXT    NOT NULL,
    ten            TEXT    NOT NULL,
    huong_dan      TEXT    NOT NULL,
    video_url      TEXT,
    -- ENGINE CONFIG (camera.py doc thang, khong con hardcode) --
    lm_a           INTEGER NOT NULL,   -- chi so landmark MediaPipe diem A
    lm_b           INTEGER NOT NULL,   -- diem B (dinh goc)
    lm_c           INTEGER NOT NULL,   -- diem C
    cam_down_angle INTEGER NOT NULL,   -- nguong goc pha "gap"
    cam_up_angle   INTEGER NOT NULL,   -- nguong goc pha "duoi"
    ideal_sec_min  REAL    NOT NULL,   -- thoi gian/rep ly tuong (min)
    ideal_sec_max  REAL    NOT NULL    -- (max)
);

CREATE TABLE patient (
    patient_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_name  TEXT    NOT NULL,
    date_of_birth TEXT,
    gender        TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE current_config (
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

CREATE TABLE session_log (
    session_log_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id      INTEGER NOT NULL,
    exercise_id     INTEGER NOT NULL,
    session_date    TEXT    NOT NULL,
    prescribed_rep  INTEGER NOT NULL,
    actual_rep      INTEGER NOT NULL,
    pain_count      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (patient_id)  REFERENCES patient(patient_id),
    FOREIGN KEY (exercise_id) REFERENCES rehab_exercises(id),
    CHECK (prescribed_rep > 0), CHECK (actual_rep >= 0), CHECK (pain_count >= 0)
);

CREATE TABLE exercise_adjustment (
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

# (khop_tap, ten, huong_dan, video_url, lm_a, lm_b, lm_c, down, up, sec_min, sec_max)
# Landmark: 11=vai T, 12=vai P, 14=khuyu P, 16=co tay P, 23=hong T, 25=goi T, 27=co chan T
EXERCISES = [
    ("đầu gối", "trượt gối",
     "Sử dụng đầu gối để co chân. Nâng đầu gối lên xuống.",
     "Bài tập Trượt gối/WIN_20260501_02_29_01_Pro.mp4",
     23, 25, 27, 60, 165, 2.5, 5.0),
    ("đầu gối", "nâng chân thẳng",
     "Giữ nguyên chân thẳng, nâng toàn bộ chân lên xuống.",
     "Bài tập Nâng chân thẳng/WIN_20260501_02_31_57_Pro.mp4",
     11, 23, 25, 145, 165, 3.0, 6.0),
    ("đầu gối", "ngồi dựa tường",
     "Đứng dựa lưng vào tường, trượt xuống đến góc đầu gối = 90°.",
     "Bài tập Ngồi dựa tường/WIN_20260501_08_11_02_Pro.mp4",
     23, 25, 27, 90, 160, 2.5, 5.0),
    ("đầu gối", "gập gối đứng",
     "Đứng thẳng, giữ tựa tay vào tường. Gập một đầu gối đưa gót về phía mông.",
     "Bài tập Gập gối đứng/WIN_20260501_02_38_23_Pro.mp4",
     23, 25, 27, 60, 160, 2.5, 5.0),
    ("khuỷu tay", "gập/duỗi khuỷu tay",
     "Gập khuỷu tay, tay để trên mặt phẳng.",
     "Bài tập Gập- Duỗi khuỷu tay/IMG_0991.mp4",   # sau khi convert .MOV -> .mp4
     12, 14, 16, 35, 165, 2.0, 4.0),
    ("khuỷu tay", "duỗi tay trên đầu",
     "Cánh tay dựng thẳng đứng, gập khuỷu ra sau đầu.",
     "Bài tập Duỗi tay trên đầu/WIN_20260501_02_17_39_Pro.mp4",
     12, 14, 16, 45, 165, 2.0, 4.0),
    ("khuỷu tay", "gập cánh tay đứng",
     "Gập khuỷu tay, duỗi hoàn toàn cánh tay.",
     None,
     12, 14, 16, 35, 165, 2.0, 4.0),
    ("khuỷu tay", "duỗi khuỷu nhờ trọng lực",
     "Giữ nguyên cánh tay lơ lửng trên không.",
     None,
     12, 14, 16, 40, 165, 2.0, 4.0),
]

conn.executemany("""
    INSERT INTO rehab_exercises
        (khop_tap, ten, huong_dan, video_url,
         lm_a, lm_b, lm_c, cam_down_angle, cam_up_angle, ideal_sec_min, ideal_sec_max)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
""", EXERCISES)

conn.execute("INSERT INTO patient (patient_name, date_of_birth, gender) "
             "VALUES ('Nguyễn Văn An','2000-05-15','Nam')")
conn.commit()
conn.close()

# In ra de xem (utf-8 de khong loi console Windows)
sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect(OUT); conn.row_factory = sqlite3.Row
print(f"Da tao: {OUT}\n")
print(f"{'id':<3}{'ten':<26}{'lm(a,b,c)':<12}{'down':<6}{'up':<5}{'sec':<10}video")
for r in conn.execute("SELECT * FROM rehab_exercises ORDER BY id"):
    lm = f"{r['lm_a']},{r['lm_b']},{r['lm_c']}"
    sec = f"{r['ideal_sec_min']}-{r['ideal_sec_max']}"
    vid = "—" if not r["video_url"] else r["video_url"].split("/")[-1]
    print(f"{r['id']:<3}{r['ten']:<26}{lm:<12}{r['cam_down_angle']:<6}{r['cam_up_angle']:<5}{sec:<10}{vid}")
conn.close()
