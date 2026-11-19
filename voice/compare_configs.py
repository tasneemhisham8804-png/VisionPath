# Compare 3 recognition settings on the clean Egyptian samples (no added noise).
import os
import re
import csv
from faster_whisper import WhisperModel

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "data", "egyptian_samples")


def normalize(text):
    text = text.lower()
    for a in "أإآ":
        text = text.replace(a, "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^a-z\u0621-\u064a]", "", text)


model = WhisperModel("small", device="cpu", compute_type="int8")

with open(os.path.join(SAMPLES, "metadata.csv"), encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))

# the hint is the list of the phrases we expect (closed vocabulary)
hint = "، ".join(row["text"] for row in rows[:20])

configs = [
    ("no hint", dict(language="ar")),
    ("hint", dict(language="ar", initial_prompt=hint)),
    ("hint + vad", dict(language="ar", initial_prompt=hint, vad_filter=True)),
]

lines = []
for name, kwargs in configs:
    ok = 0
    for row in rows:
        path = os.path.join(SAMPLES, row["file"])
        segments, info = model.transcribe(path, **kwargs)
        heard = "".join(s.text for s in segments)
        if normalize(row["text"]) in normalize(heard):
            ok += 1
    line = name + ": " + str(ok) + " / " + str(len(rows))
    print(line)
    lines.append(line)

with open("compare_configs.txt", "w") as out:
    out.write("\n".join(lines))