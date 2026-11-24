# Test: "I'm OK" answers the wellbeing question ("Are you OK?")

from output_manager import OutputManager, SYSTEM
from commands import CommandHandler

manager = OutputManager()
handler = CommandHandler(manager, lang="ar",
                         on_cancel_fall=lambda: print("  -> fall alarm cancelled"),
                         on_wellbeing_ok=lambda: print("  -> wellbeing OK"))

print("1) wellbeing question, then 'I am OK'  (should say wellbeing OK)")
handler.alert("are_you_ok", SYSTEM)
manager.wait_until_done()
handler.handle_heard("انا كويس")
manager.wait_until_done()

print("2) 'I am OK' again with nothing active  (should do nothing)")
handler.handle_heard("انا كويس")
manager.wait_until_done()

print("3) wellbeing question, then a fall alarm, then 'I am OK'  (should cancel the FALL, not wellbeing)")
handler.alert("are_you_ok", SYSTEM)
manager.wait_until_done()
handler.alert("fall_alarm", 0)
manager.wait_until_done()
handler.handle_heard("انا كويس")
manager.wait_until_done()

print("done")