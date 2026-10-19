# Fixed command grammar: match what the recognizer heard to the closest known command.

import re
from difflib import SequenceMatcher

# (phrase_id, text, intent)
GRAMMAR = [
    ("start_1", "ابدأ", "start"),
    ("start_2", "ابتدي", "start"),
    ("start_3", "يلا بينا", "start"),
    ("stop_1", "وقف", "stop"),
    ("stop_2", "قف", "stop"),
    ("stop_3", "بس كده", "stop"),
    ("repeat_1", "كرر", "repeat"),
    ("repeat_2", "عيد تاني", "repeat"),
    ("repeat_3", "قول تاني", "repeat"),
    ("help_1", "ساعدني", "help"),
    ("help_2", "اتصل بالإسعاف", "call_ambulance"),
    ("help_3", "أنا وقعت", "fell"),
    ("nav_1", "لف يمين", "turn_right"),
    ("nav_2", "لف شمال", "turn_left"),
    ("nav_3", "امشي على طول", "go_straight"),
    ("nav_4", "فين الباب", "find_door"),
]

# below this similarity we say "not understood" instead of guessing
THRESHOLD = 0.6


def normalize(text):
    text = text.lower()
    for a in "أإآ":
        text = text.replace(a, "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^a-z\u0621-\u064a]", "", text)


def match(heard):
    """Return (phrase_id, intent, score) of the closest phrase, or None."""
    h = normalize(heard)
    if h == "":
        return None
    best = None
    for phrase_id, text, intent in GRAMMAR:
        p = normalize(text)
        score = SequenceMatcher(None, h, p).ratio()
        if p in h:
            score = max(score, 0.95)
        if best is None or score > best[2]:
            best = (phrase_id, intent, score)
    if best[2] < THRESHOLD:
        return None
    return best