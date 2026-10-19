# Generate one wav file per alert phrase (Arabic and English).
# Needs internet once. After that the wav files are played locally.

import os
import numpy as np
import av
from gtts import gTTS
from scipy.io.wavfile import write
from alerts import ALERTS

RATE = 22050

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_FOLDER = os.path.join(BASE, "audio")


def mp3_to_wav(mp3_path, wav_path):
    container = av.open(mp3_path)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    chunks = []
    for frame in container.decode(audio=0):
        for f in resampler.resample(frame):
            chunks.append(f.to_ndarray().reshape(-1))
    for f in resampler.resample(None):
        chunks.append(f.to_ndarray().reshape(-1))
    container.close()
    audio = np.concatenate(chunks)
    write(wav_path, RATE, audio)


for lang in ["ar", "en"]:
    folder = os.path.join(OUT_FOLDER, lang)
    os.makedirs(folder, exist_ok=True)

    for alert_id, text_ar, text_en in ALERTS:
        if lang == "ar":
            text = text_ar
        else:
            text = text_en

        wav_path = os.path.join(folder, alert_id + ".wav")
        if os.path.exists(wav_path):
            continue

        tmp = os.path.join(folder, "tmp.mp3")
        gTTS(text=text, lang=lang).save(tmp)
        mp3_to_wav(tmp, wav_path)
        os.remove(tmp)
        print("done:", lang, alert_id)

print("finished")