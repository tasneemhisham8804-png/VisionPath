import numpy as np
import sounddevice as sd
from scipy.io.wavfile import read

print("output device:", sd.query_devices(sd.default.device[1])["name"])

rate = 22050

# test 1: a long tone (1 second)
t = np.arange(rate) / rate
tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
print("test 1: 1 second tone")
sd.play(tone, rate)
sd.wait()

# test 2: the short danger earcon after 1 second of silence
r, a = read("audio/earcons/danger.wav")
a = a.astype(np.float32) / 32768.0
b = np.concatenate([np.zeros(r, dtype=np.float32), a])
print("test 2: danger earcon after 1 second of silence")
sd.play(b, r)
sd.wait()

# test 3: the same earcon repeated 5 times in one buffer
gap = np.zeros(int(r * 0.15), dtype=np.float32)
c = np.concatenate([np.zeros(r, dtype=np.float32)] + [a, gap] * 5)
print("test 3: danger earcon 5 times in a row")
sd.play(c, r)
sd.wait()