# First run guided by voice (week 9).
# The steps and sentences are my proposal. The real first run script comes from the team.
# Nothing here touches the hotspot itself: the other module gives us hotspot_ready().

import time
import grammar
from output_manager import SYSTEM

HOTSPOT_WAIT_SECONDS = 60   # how long we wait for the hotspot each time
MAX_TRIES = 3               # how many times we repeat a step before we give up


def say(manager, key, lang):
    manager.alert("fr_" + key, SYSTEM, lang)
    manager.wait_until_done()


def ask(manager, listen, key, wanted, lang):
    """Say a step, then wait for one of the wanted commands. Wrong answer = say it again."""
    for _ in range(MAX_TRIES):
        say(manager, key, lang)
        result = grammar.match(listen())
        intent = result[1] if result else None
        if intent in wanted:
            return True
    return False


def wait_hotspot(hotspot_ready):
    start = time.time()
    while time.time() - start < HOTSPOT_WAIT_SECONDS:
        if hotspot_ready():
            return True
        time.sleep(1)
    return False


def run_first_run(manager, listen, hotspot_ready, lang="ar"):
    """listen() gives us what the user said. hotspot_ready() says if the phone is connected.
    Returns True when every step finished, False if we had to give up."""
    if not ask(manager, listen, "welcome", ("start",), lang):
        return False
    if not ask(manager, listen, "headphones", ("start",), lang):
        return False

    connected = False
    for _ in range(MAX_TRIES):
        say(manager, "hotspot", lang)
        if wait_hotspot(hotspot_ready):
            connected = True
            break
        say(manager, "hotspot_fail", lang)
    if not connected:
        return False
    say(manager, "hotspot_ok", lang)

    if not ask(manager, listen, "try", ("whats_ahead",), lang):
        return False
    say(manager, "done", lang)
    return True