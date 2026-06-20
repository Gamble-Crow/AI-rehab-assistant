"""
config.py — đường dẫn tập trung cho toàn dự án.
Mọi module dùng các hàm ở đây thay cho os.path.dirname(__file__),
để code trong app/ tìm đúng data/model/view/video dù nằm ở thư mục con.
"""
import os
import sys


def _root() -> str:
    if getattr(sys, "frozen", False):          # PyInstaller: tài nguyên đọc ở _MEIPASS
        return sys._MEIPASS
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # gốc = cha của 'app'


def _writable_root() -> str:
    if getattr(sys, "frozen", False):          # file có ghi phải cạnh .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT          = _root()
WRITABLE_ROOT = _writable_root()

VIEWS_DIR  = os.path.join(ROOT, "app", "views")
ASSETS_DIR = os.path.join(ROOT, "assets")
VIDEO_DIR  = os.path.join(ASSETS_DIR, "videos")
MODELS_DIR = os.path.join(ASSETS_DIR, "ai_models")
DATA_DIR   = os.path.join(WRITABLE_ROOT, "data")

os.makedirs(DATA_DIR, exist_ok=True)     # đảm bảo có thư mục ghi rehab.db / state
os.makedirs(MODELS_DIR, exist_ok=True)   # để model tự tải về được khi chạy dev


def view(name: str) -> str:     return os.path.join(VIEWS_DIR, name)
def model(name: str) -> str:    return os.path.join(MODELS_DIR, name)
def video(name: str) -> str:    return os.path.join(VIDEO_DIR, name)
def data(name: str) -> str:     return os.path.join(DATA_DIR, name)
def writable(name: str) -> str: return os.path.join(WRITABLE_ROOT, name)
