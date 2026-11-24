# Test: "What's ahead?" says the things in front of the user

from output_manager import OutputManager
from commands import CommandHandler

manager = OutputManager()

# fake detections: (alert_id, side)  side -1 = left, +1 = right
things = [("pole", -0.8), ("person", 0.6)]


def get_things():
    return things


handler = CommandHandler(manager, lang="ar", on_whats_ahead=get_things)

print("1) 'What is ahead' with a pole on the left and a person on the right")
print("  intent:", handler.handle_heard("إيه قدامي"))
manager.wait_until_done()

print("2) 'What is ahead' with nothing detected  (should say nothing ahead)")
things = []
print("  intent:", handler.handle_heard("إيه قدامي"))
manager.wait_until_done()

print("done")