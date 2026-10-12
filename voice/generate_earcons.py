# Generate short earcons (each one under 200 ms) as wav files.

import os
import numpy as np
from scipy.io.wavfile import write

RATE = 22050

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_FOLDER = os.path.join(BASE, "audio", "earcons")
os.makedirs(OUT_FOLDER, exist_ok=True)


def tone(freq, ms):
    n = int(RATE * ms / 1000)
    t = np.arange(n) / RATE
    wave = np.sin(2 * np.pi * freq * t)
    # short fade in and out so there is no click
    fade = int(RATE * 0.005)
    wave[:fade] *= np.linspace(0, 1, fade)
    wave[-fade:] *= np.linspace(1, 0, fade)
    return wave


def silence(ms):
    return np.zeros(int(RATE * ms / 1000))


def save(name, wave):
    audio = (wave * 0.6 * 32767).astype(np.int16)
    write(os.path.join(OUT_FOLDER, name + ".wav"), RATE, audio)
    print(name, round(len(audio) / RATE * 1000), "ms")


# one short beep: normal information
save("info", tone(880, 100))

# two beeps going up: warning
save("warning", np.concatenate([tone(660, 70), silence(20), tone(880, 70)]))

# three fast high beeps: danger
save("danger", np.concatenate([tone(1000, 45), silence(10), tone(1000, 45),
                               silence(10), tone(1000, 45)]))

# one low longer tone: system fault
save("fault", tone(300, 180))

# rising two tones: confirmation (for example "I'm OK")
save("ok", np.concatenate([tone(600, 70), silence(10), tone(900, 70)]))