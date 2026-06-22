# PhụcHồi — AI hỗ trợ tập phục hồi chức năng

Ứng dụng desktop hỗ trợ bệnh nhân tự tập phục hồi chức năng tại nhà (chấn thương gối ACL,
khuỷu tay "Tennis Elbow"...). Dùng camera đếm số lần (rep) qua góc khớp 3D, micro phát hiện
tiếng kêu đau, và một "bộ não" gợi ý điều chỉnh cường độ an toàn theo từng buổi.

## Công nghệ
- **Thị giác**: MediaPipe Pose (33 landmark 3D) — đo góc khớp, đếm rep bằng máy trạng thái.
- **Âm thanh**: YAMNet (phân loại tiếng kêu) + faster-whisper (bắt từ "đau" tiếng Việt).
- **Bộ não**: hệ luật chấm điểm (đau / tốc độ / ROM / hoàn thành) → tăng/giữ/giảm.
- **Giao diện**: HTML/CSS/JS chạy trong cửa sổ pywebview; camera stream qua MJPEG nội bộ.
- **Dữ liệu**: SQLite (`data/rehab.db`).

## Yêu cầu
- Windows 10/11 64-bit, Python 3.11+.
- Webcam + micro.
- Mạng cho **lần chạy đầu** (tự tải 2 model: pose ~30MB, YAMNet ~4MB).

## Chạy (chế độ dev)
```bash
pip install -r requirements.txt
python main.py
```
Lần đầu chạy: `data/rehab.db` tự tạo + nạp sẵn 8 bài tập; model AI tự tải nếu thiếu.

## Đóng gói .exe
```bash
pip install pyinstaller
pyinstaller main.spec
```
Kết quả ở `dist/`. Bản .exe đã nhúng sẵn pose model + YAMNet + video hướng dẫn nên
chạy được offline (file ghi `rehab.db` / `brain_states.json` nằm cạnh .exe).

## Cấu trúc
```
main.py                      điểm vào
app/
  config.py                  đường dẫn tập trung (hỗ trợ đóng gói .exe)
  controllers/api.py         API cho UI + điều phối buổi tập
  models/database.py         tầng dữ liệu SQLite
  models/brain_engine.py     bộ não đánh giá - điều chỉnh cường độ
  services/camera.py         thị giác máy tính + máy chủ MJPEG
  services/pain_detector.py  phát hiện tiếng kêu đau
  views/app.html             giao diện
data/rehab.db                database
assets/videos/               8 video hướng dẫn
assets/ai_models/            model AI (tự tải khi chạy)
```
