import sys
import numpy as np
import av
from scipy.io.wavfile import write

src = sys.argv[1]
dst = "data/noise/street.wav"
RATE = 16000

container = av.open(src)
resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
chunks = []
for frame in container.decode(audio=0):
    for f in resampler.resample(frame):
        chunks.append(f.to_ndarray().reshape(-1))
for f in resampler.resample(None):
    chunks.append(f.to_ndarray().reshape(-1))
audio = np.concatenate(chunks)
write(dst, RATE, audio)
print("saved", dst, round(len(audio) / RATE, 1), "seconds")