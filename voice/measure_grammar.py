# Accuracy per command with the fixed grammar, compared with the old substring check.

import os
import csv
from faster_whisper import WhisperModel
import grammar

BASE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(BASE, "data", "egyptian_samples")

short_ids = [g[0] for g in grammar.GRAMMAR]

model = WhisperModel("small", device="cpu", compute_type="int8")

with open(os.path.join(SAMPLES, "metadata.csv"), encoding="utf-8-sig") as f:
    rows = [r for r in csv.DictReader(f) if r["phrase_id"] in short_ids]

hint = "، ".join(g[1] for g in grammar.GRAMMAR)
print(len(rows), "recordings (long sentences excluded)")

per_command = {}
speakers = {}
lines = []
grammar_ok = 0
old_ok = 0

for row in rows:
    path = os.path.join(SAMPLES, row["file"])
    segments, info = model.transcribe(path, language="ar", initial_prompt=hint, vad_filter=True)
    heard = "".join(s.text for s in segments)

    result = grammar.match(heard)
    good = result is not None and result[0] == row["phrase_id"]
    old_good = grammar.normalize(row["text"]) in grammar.normalize(heard)

    grammar_ok += good
    old_ok += old_good
    pid = row["phrase_id"]
    per_command.setdefault(pid, [0, 0])
    per_command[pid][1] += 1
    per_command[pid][0] += good
    sp = row["speaker"]
    speakers.setdefault(sp, [0, 0])
    speakers[sp][1] += 1
    speakers[sp][0] += good

    lines.append(pid + " | expected: " + row["text"] + " | heard: " + heard +
                 " | matched: " + str(result[0] if result else None))

total = len(rows)
print()
print("grammar match :", grammar_ok, "/", total)
print("old substring :", old_ok, "/", total)
print()
for pid, (ok, n) in per_command.items():
    print(pid, ":", ok, "/", n)
print()
for sp, (ok, n) in speakers.items():
    print("speaker", sp, ":", ok, "/", n)

with open("measure_grammar.txt", "w", encoding="utf-8") as out:
    out.write("\n".join(lines))
print()
print("details saved in measure_grammar.txt")