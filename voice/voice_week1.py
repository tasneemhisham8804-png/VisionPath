# Week 1 - voice commands (start / stop / repeat) in Arabic and English

import sounddevice as sd
from scipy.io.wavfile import write
from faster_whisper import WhisperModel
from gtts import gTTS
import pygame
import time

# settings
RATE = 16000
SECONDS = 3

# load the speech model (downloads the first time)
print("loading model...")
model = WhisperModel("small", device="cpu", compute_type="int8")

# start the audio player
pygame.mixer.init()

# the last thing we said, so "repeat" can say it again
last_text = ""
last_lang = "en"


def record():
    print("speak now...")
    audio = sd.rec(SECONDS * RATE, samplerate=RATE, channels=1, dtype="int16")
    sd.wait()
    write("input.wav", RATE, audio)


def listen():
    record()
    text = ""
    for lang in ["ar", "en"]:
        # give the model a hint about the words we expect
        if lang == "ar":
            hint = "ابدأ، وقف، كرر"
        else:
            hint = "start, stop, repeat"

        segments, info = model.transcribe("input.wav", language=lang, initial_prompt=hint)
        text = ""
        for s in segments:
            text = text + s.text
        text = text.lower()

        # if we found a command in this language, stop here
        if get_command(text) != "unknown":
            return text, lang

    return text, "en"



def speak(text, lang):
    global last_text, last_lang
    last_text = text
    last_lang = lang
    tts = gTTS(text=text, lang=lang)
    tts.save("output.mp3")
    pygame.mixer.music.load("output.mp3")
    pygame.mixer.music.play()
    while pygame.mixer.music.get_busy():
        time.sleep(0.1)
    pygame.mixer.music.unload()


def get_command(text):
    if "start" in text or "ابدأ" in text or "ابدا" in text:
        return "start"
    if "stop" in text or "وقف" in text:
        return "stop"
    if "repeat" in text or "كرر" in text or "عيد" in text:
        return "repeat"
    return "unknown"


# main loop
while True:
    input("press Enter then talk: ")
    start_time = time.time()

    text, lang = listen()
    command = get_command(text)

    print("heard:", text)
    print("language:", lang)
    print("command:", command)
    print("time:", round(time.time() - start_time, 2), "seconds")

    if lang != "ar":
        lang = "en"

    if command == "start":
        if lang == "ar":
            speak("تم بدء الملاحة", "ar")
        else:
            speak("Navigation started", "en")
    elif command == "stop":
        if lang == "ar":
            speak("تم إيقاف الملاحة", "ar")
        else:
            speak("Navigation stopped", "en")
    elif command == "repeat":
        if last_text == "":
            speak("Nothing to repeat", "en")
        else:
            speak(last_text, last_lang)
    else:
        if lang == "ar":
            speak("لم أفهم الأمر", "ar")
        else:
            speak("I did not understand", "en")
