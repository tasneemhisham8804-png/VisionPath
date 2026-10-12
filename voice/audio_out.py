# Audio output. One stream stays open all the time so the sound device never goes to sleep.
# Alerts and earcons are played in stereo.
# side: -1 = left, 0 = center, 1 = right

import os
import time
import threading
import numpy as np
import sounddevice as sd
from scipy.io.wavfile import read

BASE = os.path.dirname(os.path.abspath(__file__))
AUDIO_FOLDER = os.path.join(BASE, "audio")
RATE = 22050

# set to True if the headphones play left and right the wrong way round
SWAP_CHANNELS = False

# a tiny noise (not audible) played between sounds so the device stays awake
KEEP_AWAKE_LEVEL = 0.0003


class Player:
    def __init__(self):
        self.buffer = np.zeros((0, 2), dtype=np.float32)
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(samplerate=RATE, channels=2,
                                      dtype="float32", callback=self.fill)
        self.stream.start()
        time.sleep(1.0)  # let the device wake up once

    def fill(self, outdata, frames, time_info, status):
        with self.lock:
            n = min(frames, len(self.buffer))
            outdata[:n] = self.buffer[:n]
            self.buffer = self.buffer[n:]
        if n < frames:
            outdata[n:] = np.random.uniform(-KEEP_AWAKE_LEVEL, KEEP_AWAKE_LEVEL,
                                            (frames - n, 2))

    def play(self, audio, side=0.0, wait=True):
        stereo = make_stereo(audio, side).astype(np.float32)
        with self.lock:
            self.buffer = np.concatenate([self.buffer, stereo])
        if wait:
            while len(self.buffer) > 0:
                time.sleep(0.01)
            time.sleep(0.05)

    def stop(self):
        with self.lock:
            self.buffer = np.zeros((0, 2), dtype=np.float32)


_player = None


def get_player():
    global _player
    if _player is None:
        _player = Player()
    return _player


def make_stereo(audio, side):
    # move the sound between left and right, keeping the total loudness similar
    if SWAP_CHANNELS:
        side = -side
    angle = (side + 1) * np.pi / 4
    left = audio * np.cos(angle)
    right = audio * np.sin(angle)
    return np.stack([left, right], axis=1)


def load_wav(path):
    rate, audio = read(path)
    if rate != RATE:
        raise ValueError("wav rate is " + str(rate) + ", expected " + str(RATE))
    return audio.astype(np.float32) / 32768.0


def play_alert(alert_id, lang="ar", side=0.0, wait=True):
    path = os.path.join(AUDIO_FOLDER, lang, alert_id + ".wav")
    get_player().play(load_wav(path), side, wait)


def play_earcon(name, side=0.0, wait=True):
    path = os.path.join(AUDIO_FOLDER, "earcons", name + ".wav")
    get_player().play(load_wav(path), side, wait)