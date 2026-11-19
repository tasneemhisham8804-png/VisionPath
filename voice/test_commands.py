import time
from output_manager import OutputManager, EMERGENCY, HAZARD
from commands import CommandHandler
import commands

commands.CONFIRM_SECONDS = 3   # shorter wait for the test

manager = OutputManager()
handler = CommandHandler(
    manager,
    on_cancel_fall=lambda: print("  -> fall alarm cancelled"),
    on_call_ambulance=lambda: print("  -> CALLING AMBULANCE"),
    on_choose_contact=lambda: print("  -> contact menu opened"),
)

print("1) fall alarm, then 'I am OK'")
handler.alert("fall_alarm", EMERGENCY)
manager.wait_until_done()
print("  intent:", handler.handle_heard("أنا كويس"))
manager.wait_until_done()

print("2) a hazard, then 'why'")
handler.alert("curb", HAZARD, side=1)
manager.wait_until_done()
print("  intent:", handler.handle_heard("ليه"))
manager.wait_until_done()

print("3) ambulance, then 'yes'  (should call)")
handler.handle_heard("اتصل بالإسعاف")
manager.wait_until_done()
print("  intent:", handler.handle_heard("نعم"))
manager.wait_until_done()

print("4) ambulance, then 'no'  (should NOT call)")
handler.handle_heard("اتصل بالإسعاف")
manager.wait_until_done()
print("  intent:", handler.handle_heard("لا"))
manager.wait_until_done()

print("5) ambulance, then unclear speech (should NOT call)")
handler.handle_heard("اتصل بالإسعاف")
manager.wait_until_done()
print("  intent:", handler.handle_heard("كلام مالوش علاقة"))
manager.wait_until_done()

print("6) ambulance, then silence for 4 seconds (should cancel)")
handler.handle_heard("اتصل بالإسعاف")
manager.wait_until_done()
time.sleep(4)
handler.check_timeout()
manager.wait_until_done()
print("done")
