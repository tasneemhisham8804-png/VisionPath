# Week 2 - record Egyptian Arabic voice samples for accent tuning

import os
import csv
import time
import sounddevice as sd
from scipy.io.wavfile import write

RATE = 16000
SECONDS = 5
TAKES = 1   # how many times each person says each phrase

# save the samples next to this script (not in the folder we ran python from)
BASE = os.path.dirname(os.path.abspath(__file__))
SAVE_FOLDER = os.path.join(BASE, "data", "egyptian_samples")
CSV_FILE = os.path.join(SAVE_FOLDER, "metadata.csv")

# (id, text)
phrases = [
    ("start_1", "ابدأ"),
    ("start_2", "ابتدي"),
    ("start_3", "يلا بينا"),
    ("stop_1", "وقف"),
    ("stop_2", "قف"),
    ("stop_3", "بس كده"),
    ("repeat_1", "كرر"),
    ("repeat_2", "عيد تاني"),
    ("repeat_3", "قول تاني"),
    ("help_1", "ساعدني"),
    ("help_2", "اتصل بالإسعاف"),
    ("help_3", "أنا وقعت"),
    ("nav_1", "لف يمين"),
    ("nav_2", "لف شمال"),
    ("nav_3", "امشي على طول"),
    ("nav_4", "فين الباب"),
    ("long_1", "في عقبة قدامي ومش عارف أعدي"),
    ("long_2", "عايز أروح الشارع اللي جنب البيت"),
    ("long_3", "ممكن تقولي أنا فين دلوقتي"),
    ("long_4", "وقف الإرشاد لحد ما أقولك كمل"),
]


def record(path):
    print("recording... speak now")
    audio = sd.rec(SECONDS * RATE, samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    write(path, RATE, audio)

    loudest = int(abs(audio.astype("int32")).max())
    print("saved:", os.path.basename(path), "| loudness:", loudest)
    if loudest < 1000:
        print("WARNING: very quiet, check the microphone or speak louder")

    print("playing back...")
    sd.play(audio, RATE)
    sd.wait()


def add_to_csv(row):
    new_file = not os.path.exists(CSV_FILE)
    with open(CSV_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["speaker", "gender", "age_group", "phrase_id",
                             "text", "take", "file", "date"])
        writer.writerow(row)


os.makedirs(SAVE_FOLDER, exist_ok=True)

print("=== Egyptian Arabic voice samples ===")
print("Tell the volunteer: their voice is only used for the graduation project.")
consent = input("Did the volunteer agree to be recorded? (y/n): ")
if consent.lower() != "y":
    print("No consent, stopping.")
    raise SystemExit

# we use an ID, not the real name
speaker = input("Speaker ID (example s01): ")
gender = input("Gender (m/f): ")
age_group = input("Age group (example 18-25): ")

speaker_folder = os.path.join(SAVE_FOLDER, speaker)
os.makedirs(speaker_folder, exist_ok=True)

for phrase_id, text in phrases:
    for take in range(1, TAKES + 1):
        while True:
            print()
            print("Say:", text, "  (take", take, "of", TAKES, ")")
            input("Press Enter, then speak... ")
            file_name = speaker + "_" + phrase_id + "_t" + str(take) + ".wav"
            path = os.path.join(speaker_folder, file_name)
            record(path)
            answer = input("Enter = keep, r = record again: ")
            if answer.lower() != "r":
                break
        add_to_csv([speaker, gender, age_group, phrase_id, text, take,
                    os.path.join(speaker, file_name), time.strftime("%Y-%m-%d")])

print()
print("Done! Saved in:", speaker_folder)