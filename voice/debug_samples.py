import os
import csv
from faster_whisper import WhisperModel

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "data", "egyptian_samples")

model = WhisperModel("small", device="cpu", compute_type="int8")
with open(os.path.join(SAMPLES, "metadata.csv"), encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

lines = ["first row: " + str(rows[0])]
for row in rows[:6]:
    path = os.path.join(SAMPLES, row["file"])
    segments, info = model.transcribe(path, language="ar")
    heard = "".join(s.text for s in segments)
    lines.append("expected: " + row["text"] + " | heard: " + heard)

with open("debug.txt", "w", encoding="utf-8") as out:
    out.write("\n".join(lines))
print("done, open debug.txt")