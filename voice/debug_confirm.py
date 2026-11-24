import time
import grammar
from output_manager import OutputManager
from commands import CommandHandler

print("match:", grammar.match("اتصل بالإسعاف"))

manager = OutputManager()
handler = CommandHandler(manager, on_call_ambulance=lambda: print("CALLING AMBULANCE"))

print("step 1: ask")
intent = handler.handle_heard("اتصل بالإسعاف")
print("  intent:", intent)
print("  waiting until:", handler.waiting_confirm_until, "| now:", time.time())

print("step 2: say yes")
intent = handler.handle_heard("نعم")
print("  intent:", intent)
manager.wait_until_done()
print("done")