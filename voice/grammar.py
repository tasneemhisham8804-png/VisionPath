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
# extra commands for the app and the emergency flow (not part of the measured 16 phrases)
EXTRA = [
    ("im_ok_1", "أنا بخير", "im_ok"),
    ("im_ok_2", "أنا كويس", "im_ok"),
    ("im_ok_3", "أنا تمام", "im_ok"),
    ("why_1", "ليه", "why"),
    ("why_2", "ليه كده", "why"),
    ("contact_1", "اتصل بحد", "choose_contact"),
    ("contact_2", "اختار جهة اتصال", "choose_contact"),
    ("yes_1", "نعم", "yes"),
    ("yes_2", "أيوه", "yes"),
    ("no_1", "لا", "no"),
]
# below this similarity we say "not understood" instead of guessing
THRESHOLD = 0.6


def normalize(text):
    text = text.lower()
    for a in "أإآ":
        text = text.replace(a, "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    return re.sub(r"[^a-z\u0621-\u064a]", "", text)


def words(text):
    return [normalize(w) for w in text.split() if normalize(w)]


def contains(heard_words, phrase_words):
    n = len(phrase_words)
    for i in range(len(heard_words) - n + 1):
        if heard_words[i:i + n] == phrase_words:
            return True
    return False


def match(heard, use_extra=True):
    """Return (phrase_id, intent, score) of the closest phrase, or None."""
    h = normalize(heard)
    if h == "":
        return None
    hw = words(heard)
    phrases = GRAMMAR + EXTRA if use_extra else GRAMMAR
    best = None
    for phrase_id, text, intent in phrases:
        p = normalize(text)
        score = SequenceMatcher(None, h, p).ratio()
        # the whole phrase must appear as whole words, not inside another word
        if contains(hw, words(text)):
            score = max(score, 0.95)
        if best is None or score > best[2]:
            best = (phrase_id, intent, score)
    if best[2] < THRESHOLD:
        return None
    return best