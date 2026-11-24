# make the Arabic alert sounds with Piper (offline voice)
import os
import wave
from piper import PiperVoice
from alerts import ALERTS

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "audio", "ar_piper")
os.makedirs(OUT, exist_ok=True)

voice = PiperVoice.load(os.path.join(BASE, "piper_voice", "ar_JO-kareem-medium.onnx"))

for alert_id, ar, en in ALERTS:
    path = os.path.join(OUT, alert_id + ".wav")
    with wave.open(path, "wb") as f:
        voice.synthesize_wav(ar, f)
    print("made", alert_id)

print("done:", len(ALERTS), "files in", OUT)