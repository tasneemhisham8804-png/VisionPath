# Test: first run guided by voice, with fake answers and a fake hotspot

from output_manager import OutputManager
from first_run import run_first_run

manager = OutputManager()


def make_listen(answers):
    """Gives the answers one by one, like a user who speaks."""
    answers = list(answers)

    def listen():
        heard = answers.pop(0)
        print("  user says:", heard)
        return heard
    return listen


# the hotspot says "not ready" twice, then "ready"
checks = {"count": 0}


def hotspot_ready():
    checks["count"] += 1
    return checks["count"] >= 3


print("1) user follows every step  (should finish: True)")
ok = run_first_run(manager, make_listen(["ابدأ", "ابدأ", "إيه قدامي"]), hotspot_ready)
print("  finished:", ok)

print("2) user says nonsense 3 times at the start  (should give up: False)")
ok = run_first_run(manager, make_listen(["كلام", "كلام", "كلام"]), hotspot_ready)
print("  finished:", ok)

print("done")