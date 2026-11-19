# how long does Piper take to make a sentence?
import wave
import time
from piper import PiperVoice

voice = PiperVoice.load("piper_voice/ar_JO-kareem-medium.onnx")

text = "انتبه، عائق أمامك"

for i in range(3):
    start = time.time()
    with wave.open("speed_test.wav", "wb") as f:
        voice.synthesize_wav(text, f)
    print("try", i + 1, ":", round(time.time() - start, 2), "seconds")