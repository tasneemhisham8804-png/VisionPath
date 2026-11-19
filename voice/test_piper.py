# test Piper (offline voice)
import wave
from piper import PiperVoice

voice = PiperVoice.load("piper_voice/ar_JO-kareem-medium.onnx")

text = "انتبه، عائق أمامك"

with wave.open("test_piper.wav", "wb") as f:
    voice.synthesize_wav(text, f)

print("saved test_piper.wav")