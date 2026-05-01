import sounddevice as sd
import numpy as np
import queue
import threading
import re

#faster-whisper 
from faster_whisper import WhisperModel


# cài đặt
SAMPLE_RATE      = 16000   # Hz
CHUNK_DURATION   = 1.5     # giay — ngan hon v2, phan hoi nhanh hon
WHISPER_MODEL    = "base"  # base (145 mb) chinh xac hon tiny và co ho tro tieng viet
PAIN_THRESHOLD   = 2.0     # nguong tong diem de tính la "keu dau"

# Nguong am luong (RMS). Am thanh binh thuong ~0.02-0.05, tieng keu ~0.15+
# Chinh giam neu phong im lang, chinh tang neu phong on ao
ENERGY_THRESHOLD = 0.12


#  SCORING SYSTEM: dung co che cong diem thay vì dung tu dien phang
# Tong diem >= PAIN_THRESHOLD → keu dau.
# Logic: tieng "oi" mot minh co the la goi ban (diem thap = 0.8),
# nhung "oi dau qua" → 0.8 + 1.5 = 2.3 → vuot nguong.
# Acoustic burst (tieng to dot ngot) cong them diem.
# Tat ca pattern deu lowercase, khong dau (viet thuong + ascii)
# vi Whisper tiny tieng Viet hay mat dau thanh.

PAIN_PATTERNS: list[tuple[float, str]] = [
    # BIEU LO DAU MANH (diem cao)
    (2.5, r"\bđau\s+quá\b"),          # "dau qua"
    (2.5, r"\bôi\s+trời\b"),          # "oi troi oi"
    (2.5, r"\btrời\s+ơi\b"),
    (2.0, r"\bđau\s+quá\s+rồi\b"),
    (2.0, r"\bouch\b"),
    (2.0, r"\bargh\b"),
    (2.0, r"\bai\s+da\b"),            # "ai da"
    (2.0, r"\bđau\b"),                # "dau" don le

    # TU DON (diem vua)
    (1.5, r"\bui\s+da\b"),
    (1.2, r"\bow\b"),
    (1.2, r"\bugh\b"),

    # TIENG KEU / AM THANH (diem thap, thuong kem acoustic)
    (0.8, r"\boi\b"),                 # "oi" co the la goi ban → diem thap
    (0.8, r"\bai\b"),                 # "ai" co the la cau hoi "ai day?"
    (0.8, r"\bah+\b"),                # "ah", "ahh", "ahhh"
    (0.8, r"\boh+\b"),
    (0.6, r"\bum+\b"),
    (1.0, r"\ba{2,}\b"),              # "aaa", "aaaa" — keo dai
    (1.0, r"\bo{2,}\b"),              # "ooo"
    (0.8, r"\bu{2,}\b"),
]

# Compile truoc de nhanh hon
_COMPILED: list[tuple[float, re.Pattern]] = [
    (score, re.compile(pat, re.IGNORECASE))
    for score, pat in PAIN_PATTERNS
]


def score_text(text: str) -> tuple[float, list[str]]:
    """
    Tinh tong diem cua mot doan text.
    Tra ve (tong_diem, danh_sach_pattern_khop).
    Moi pattern chi tinh 1 lan dù xuat hien nhieu lan.
    """
    total  = 0.0
    hits   = []
    for score, pattern in _COMPILED:
        if pattern.search(text):
            total += score
            hits.append(pattern.pattern)
    return total, hits

class PainDetector:
    # Ket hop hai tin hieu doc lap:
    #  1. Text score  : Whisper → text → scoring
    #  2. Acoustic    : RMS energy cua doan am thanh
    # Quyet dinh cuoi: is_pain = (text_score >= threshold) OR (text_score >= threshold*0.6 AND acoustic_burst)
    # acoustic_burst" giup bat duoc tieng "ahhh" ma Whisper nhan dang sai thanh chu nao do vo nghia, nhung am luong ro rang la keu.

    def analyze(
        self,
        text: str,
        audio_rms: float,
    ) -> tuple[bool, float, str]:
        #Tra ve (is_pain, score, reason_string)
        text_score, hits = score_text(text)
        acoustic_burst   = audio_rms >= ENERGY_THRESHOLD

        # Quyet dinh
        if text_score >= PAIN_THRESHOLD:
            is_pain = True
            reason  = f"score={text_score:.1f} [{', '.join(hits)}]"
        elif text_score >= PAIN_THRESHOLD * 0.6 and acoustic_burst:
            # Van ban nua chung + am thanh to → van tinh
            is_pain = True
            reason  = f"score={text_score:.1f} + acoustic burst (rms={audio_rms:.3f})"
        else:
            is_pain = False
            parts   = []
            if text_score > 0:
                parts.append(f"score={text_score:.1f}")
            if acoustic_burst:
                parts.append(f"rms={audio_rms:.3f}")
            reason = ", ".join(parts) if parts else "—"

        return is_pain, text_score, reason


#  MODEL GHI NHAN 
class PainCryCounter:
    #Luu tru su kien, tinh thong ke. Chi giu trong RAM.

    def __init__(self):
        self.detector    = PainDetector()
        self.pain_count  = 0

    def record(self, text: str, audio_rms: float) -> bool:
        """Tra ve True neu phat hien keu dau, dong thoi cap nhat pain_count."""
        text = text.strip()
        if not text:
            return False
        is_pain, _, _ = self.detector.analyze(text, audio_rms)
        if is_pain:
            self.pain_count += 1
        return is_pain


#  SPEECH RECOGNIZER 
class SpeechRecognizer:

    def __init__(self, model_size: str = WHISPER_MODEL):
        self.model       = WhisperModel(model_size, device="cpu", compute_type="int8")
        self.audio_queue : queue.Queue = queue.Queue()
        self.running     = False
        print("[*] Tai model xong!")

    def _audio_callback(self, indata, frames, time, status):
        if status:
            print(f"[!] {status}")
        self.audio_queue.put(indata.copy())

    def _transcribe(self, audio_np: np.ndarray) -> tuple[str, float]:
        """Tra ve (text, rms_energy)."""
        audio_float = audio_np.flatten().astype(np.float32)
        rms         = float(np.sqrt(np.mean(audio_float ** 2)))

        # Chuan hoa truoc khi dua vao Whisper
        max_val = np.max(np.abs(audio_float))
        if max_val > 1e-6:
            audio_float = audio_float / max_val

        # faster-whisper tra ve generator (segments)
        segments, _ = self.model.transcribe(
            audio_float,
            language="vi",
            beam_size=1,          # nhanh nhat, du tot voi tiny
            vad_filter=True,      # bo qua doan im lang tu dong
            vad_parameters={"min_silence_duration_ms": 300},
        )
        text = " ".join(seg.text for seg in segments).strip()
        return text, rms

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
                        chunk        = self.audio_queue.get(timeout=1)
                        text, rms    = self._transcribe(chunk)
                        callback(text, rms)
                    except queue.Empty:
                        continue
                    except Exception as ex:
                        print(f"[!] Loi: {ex}")

        threading.Thread(target=_loop, daemon=True).start()

    def stop(self):
        self.running = False


#  MAIN 
def main():
    print("[*] Dang tai faster-whisper tiny...")
    counter    = PainCryCounter()
    recognizer = SpeechRecognizer(WHISPER_MODEL)

    def on_audio(text: str, rms: float):
        if counter.record(text, rms):
            print(f"KEU DAU: {counter.pain_count}")

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
