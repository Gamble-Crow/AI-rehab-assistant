import sounddevice as sd
import numpy as np
import queue
import threading
import re
import os
import time
import urllib.request

# faster-whisper (lop PHU: bat tu "dau", ...)
from faster_whisper import WhisperModel

# YAMNet qua MediaPipe AudioClassifier (lop CHINH: bat tieng keu)
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import audio as mp_audio
from mediapipe.tasks.python.components import containers as mp_containers


# ── Cai dat ──────────────────────────────────────────────────────────────────
SAMPLE_RATE      = 16000   # Hz (YAMNet + Whisper deu dung 16k)
CHUNK_DURATION   = 1.5     # giay moi doan xu ly
WHISPER_MODEL    = "base"  # lop phu -> uu tien toc do (YAMNet lo phan chinh)

ENERGY_GATE      = 0.01    # rms duoi nguong nay = im lang -> bo qua ca 2 model
PAIN_THRESHOLD   = 2.0     # tong diem text de tinh la "tu keu dau"
YAMNET_THRESHOLD = 0.25    # diem lop "tieng dau" cua YAMNet de tinh la keu dau
SPEECH_GATE      = 0.30    # diem "Speech" toi thieu de chay Whisper (tieng keu/nhieu -> khong transcribe)
PAIN_COOLDOWN_S  = 2.0     # khong dem lai trong N giay -> tranh dem 1 tieng keu nhieu lan

# Cac lop am thanh (AudioSet/YAMNet) coi la "tieng dau"
PAIN_SOUND_CLASSES = {
    "Screaming", "Yell", "Shout", "Bellow", "Children shouting",
    "Groan", "Grunt", "Whimper", "Wail, moan",
    "Crying, sobbing", "Baby cry, infant cry",
}


# ── He thong cham diem TU (Whisper -> text) ──────────────────────────────────
# Tong diem >= PAIN_THRESHOLD -> tu keu dau. "dau" don le = 2.0 -> du nguong.
PAIN_PATTERNS: list[tuple[float, str]] = [
    (2.5, r"\bđau\s+quá\b"),
    (2.5, r"\bôi\s+trời\b"),
    (2.5, r"\btrời\s+ơi\b"),
    (2.0, r"\bđau\s+quá\s+rồi\b"),
    (2.0, r"\bouch\b"),
    (2.0, r"\bargh\b"),
    (2.0, r"\bai\s+da\b"),
    (2.0, r"\bđau\b"),
    (1.5, r"\bui\s+da\b"),
    (1.2, r"\bow\b"),
    (1.2, r"\bugh\b"),
    (0.8, r"\boi\b"),
    (0.8, r"\bai\b"),
    (0.8, r"\bah+\b"),
    (0.8, r"\boh+\b"),
    (0.6, r"\bum+\b"),
    (1.0, r"\ba{2,}\b"),
    (1.0, r"\bo{2,}\b"),
    (0.8, r"\bu{2,}\b"),
]
_COMPILED: list[tuple[float, re.Pattern]] = [
    (score, re.compile(pat, re.IGNORECASE)) for score, pat in PAIN_PATTERNS
]


def score_text(text: str) -> tuple[float, list[str]]:
    """Tong diem cua mot doan text + danh sach pattern khop (moi pattern tinh 1 lan)."""
    total, hits = 0.0, []
    for score, pattern in _COMPILED:
        if pattern.search(text):
            total += score
            hits.append(pattern.pattern)
    return total, hits


def ensure_yamnet(path: str = "yamnet.tflite") -> str:
    if os.path.exists(path):
        return path
    url = ("https://storage.googleapis.com/mediapipe-models/"
           "audio_classifier/yamnet/float32/latest/yamnet.tflite")
    print("[INFO] Tai model YAMNet (~4MB)...")
    urllib.request.urlretrieve(url, path)
    return path


# ── Lop CHINH: phan loai tieng keu (YAMNet) ──────────────────────────────────
class SoundPainDetector:
    _ALLOW = list(PAIN_SOUND_CLASSES) + ["Speech"]

    def __init__(self):
        base = mp_python.BaseOptions(model_asset_path=ensure_yamnet())
        opts = mp_audio.AudioClassifierOptions(
            base_options=base,
            category_allowlist=self._ALLOW,
            max_results=len(self._ALLOW),
        )
        self.clf = mp_audio.AudioClassifier.create_from_options(opts)
        print("[*] YAMNet san sang!")

    def classify(self, audio_float: np.ndarray) -> tuple[float, str, float]:
        """Tra ve (cry_score, cry_label, speech_score)."""
        try:
            clip    = mp_containers.AudioData.create_from_array(audio_float, SAMPLE_RATE)
            results = self.clf.classify(clip)
        except Exception:
            return 0.0, "", 0.0
        cry, label, speech = 0.0, "", 0.0
        for res in results:
            for cat in res.classifications[0].categories:
                if cat.category_name == "Speech":
                    speech = max(speech, cat.score)
                elif cat.score > cry:           # con lai deu la lop tieng dau (do allowlist)
                    cry, label = cat.score, cat.category_name
        return cry, label, speech


# ── Gop tin hieu: tieng keu (chinh) OR tu dau (phu) ──────────────────────────
class PainDetector:
    def analyze(self, text: str, cry_score: float) -> tuple[bool, str]:
        """Tra ve (is_pain, reason)."""
        text_score, hits = score_text(text)
        cry_pain  = cry_score >= YAMNET_THRESHOLD
        word_pain = text_score >= PAIN_THRESHOLD
        is_pain   = cry_pain or word_pain

        if cry_pain and word_pain:
            reason = f"tieng keu({cry_score:.2f}) + tu[{','.join(hits)}]"
        elif cry_pain:
            reason = f"tieng keu (cry={cry_score:.2f})"
        elif word_pain:
            reason = f"tu dau [{','.join(hits)}] score={text_score:.1f}"
        else:
            reason = f"cry={cry_score:.2f} text={text_score:.1f}"
        return is_pain, reason


# ── Ghi nhan + cooldown ──────────────────────────────────────────────────────
class PainCryCounter:
    def __init__(self):
        self.detector    = PainDetector()
        self.pain_count  = 0
        self._last_count = 0.0

    def record(self, text: str, audio_rms: float, cry_score: float = 0.0) -> bool:
        """True neu tinh la 1 lan keu dau MOI (da qua cooldown)."""
        is_pain, _ = self.detector.analyze(text.strip(), cry_score)
        if not is_pain:
            return False
        now = time.time()
        if now - self._last_count >= PAIN_COOLDOWN_S:
            self.pain_count += 1
            self._last_count = now
            return True
        return False   # van dau nhung trong cooldown -> khong dem trung


# ── Thu mic + chay 2 model ───────────────────────────────────────────────────
class SpeechRecognizer:
    def __init__(self, model_size: str = WHISPER_MODEL):
        self.model       = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.sound       = SoundPainDetector()
        self.audio_queue : queue.Queue = queue.Queue()
        self.running     = False
        print("[*] Tai model xong!")

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            print(f"[!] {status}")
        self.audio_queue.put(indata.copy())

    def _process(self, audio_np: np.ndarray) -> tuple[str, float, float, str, float]:
        """Tra ve (text, rms, cry_score, cry_label, speech_score)."""
        audio_float = audio_np.flatten().astype(np.float32)
        rms         = float(np.sqrt(np.mean(audio_float ** 2)))

        if rms < ENERGY_GATE:                 # im lang -> bo qua ca 2 model
            return "", rms, 0.0, "", 0.0

        # CHINH: YAMNet tren am thanh GOC (chua chuan hoa)
        cry_score, cry_label, speech = self.sound.classify(audio_float)

        # PHU: CHI chay Whisper khi YAMNet thay co tieng NOI
        # -> tieng keu/nhieu khong bi transcribe thanh chu bia
        text = ""
        if speech >= SPEECH_GATE:
            max_val = np.max(np.abs(audio_float))
            norm    = audio_float / max_val if max_val > 1e-6 else audio_float
            segments, _ = self.model.transcribe(
                norm,
                language="vi",
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
            )
            text = " ".join(seg.text for seg in segments).strip()
        return text, rms, cry_score, cry_label, speech

    def start(self, callback):
        self.running = True

        def _loop():
            with sd.InputStream(
                samplerate = SAMPLE_RATE,
                channels   = 1,
                dtype      = "float32",
                blocksize  = int(SAMPLE_RATE * CHUNK_DURATION),
                callback   = self._audio_callback,
            ):
                print(f"[*] Lang nghe micro... (moi doan {CHUNK_DURATION}s)")
                while self.running:
                    try:
                        chunk = self.audio_queue.get(timeout=1)
                        text, rms, cry, label, speech = self._process(chunk)
                        callback(text, rms, cry, label, speech)
                    except queue.Empty:
                        continue
                    except Exception as ex:
                        print(f"[!] Loi: {ex}")

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        self.running = False


# ── MAIN (chay thu doc lap) ──────────────────────────────────────────────────
def main():
    print("[*] Dang tai model...")
    counter    = PainCryCounter()
    recognizer = SpeechRecognizer(WHISPER_MODEL)

    def on_audio(text: str, rms: float, cry: float, label: str, speech: float):
        new = counter.record(text, rms, cry)
        if text.strip() or cry > 0.1:
            print(f"[MIC] text='{text}' rms={rms:.3f} cry={cry:.2f}({label}) "
                  f"speech={speech:.2f} dau={new} tong={counter.pain_count}")

    recognizer.start(callback=on_audio)
    print("[*] Dang lang nghe... (Ctrl+C de thoat)\n")
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        pass
    recognizer.stop()
    print(f"\nKet qua: {counter.pain_count} lan keu dau.")


if __name__ == "__main__":
    main()
