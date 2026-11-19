import numpy as np
import sounddevice as sd
from scipy.io.wavfile import read

rate, audio = read("audio/ar/obstacle.wav")
audio = audio.astype(np.float32) / 32768.0
silent = np.zeros_like(audio)

print("LEFT channel only")
sd.play(np.stack([audio, silent], axis=1), rate)
sd.wait()