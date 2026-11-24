# Measure recognition accuracy and time on the Egyptian samples, clean and with noise.
# If data/noise/street.wav exists it is used as the noise, otherwise white noise (synthetic).

import os
import re
import csv
import time
import numpy as np
from scipy.io.wavfile import read, write
from faster_whisper import WhisperModel

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "data", "egyptian_samples")
NOISE_FILE = os.path.join(BASE, "data", "noise", "street.wav")
TMP = os.path.join(BASE, "tmp_noisy.wav")

SNR_LIST = [None, 20, 10, 5]   # None = clean


def normalize(text):
    # keep only letters, make a few Arabic letters the same
    text = text.lower()
    for a in "أإآ":
        text = text.replace(a, "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^a-z\u0621-\u064a]", "", text)


def get_noise(length):
    if os.path.exists(NOISE_FILE):
        rate, n = read(NOISE_FILE)
        if n.ndim > 1:
            n = n[:, 0]
        n = n.astype(np.float32)
        n = np.resize(n, length)   # repeat if too short
        return n, "real street noise"
    return np.random.randn(length).astype(np.float32), "synthetic white noise"


def add_noise(audio, snr_db):
    audio = audio.astype(np.float32)
    noise, kind = get_noise(len(audio))
    p_audio = np.mean(audio ** 2)
    p_noise = np.mean(noise ** 2) + 1e-9
    scale = np.sqrt(p_audio / (p_noise * 10 ** (snr_db / 10)))
    mixed = audio + noise * scale
    mixed = np.clip(mixed, -32768, 32767)
    return mixed.astype(np.int16), kind


print("loading model...")
model = WhisperModel("small", device="cpu", compute_type="int8")

rows = []
with open(os.path.join(SAMPLES, "metadata.csv"), encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        rows.append(row)

print(len(rows), "samples")
hint = "، ".join(row["text"] for row in rows[:20])
results = {}
noise_kind = ""

for snr in SNR_LIST:
    ok = 0
    times = []
    for row in rows:
        rate, audio = read(os.path.join(SAMPLES, row["file"]))
        if snr is not None:
            audio, noise_kind = add_noise(audio, snr)
        write(TMP, rate, audio)

        start = time.time()
        segments, info = model.transcribe(TMP, language="ar", initial_prompt=hint, vad_filter=True)
        heard = "".join(s.text for s in segments)
        times.append(time.time() - start)

        if normalize(row["text"]) in normalize(heard):
            ok += 1
    name = "clean" if snr is None else str(snr) + " dB"
    results[name] = (ok, len(rows), sum(times) / len(times))
    print(name, ":", ok, "/", len(rows), "| avg time", round(sum(times) / len(times), 2), "s")

os.remove(TMP)
print()
print("noise used:", noise_kind if noise_kind else "none")