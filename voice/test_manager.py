import time
from output_manager import OutputManager, EMERGENCY, HAZARD, SYSTEM, INFO


def fake_vibrate(level, side):
    print("  vibrate: level", level, "side", side)


manager = OutputManager(vibrate=fake_vibrate)

print("test 1: one hazard on the left")
manager.alert("obstacle", HAZARD, "ar", side=-1)
manager.wait_until_done()
time.sleep(0.5)

print("test 2: a weather warning, cut by the fall alarm")
manager.alert("weather", SYSTEM, "ar")
time.sleep(0.7)
manager.alert("fall_alarm", EMERGENCY, "ar")
manager.wait_until_done()
time.sleep(0.5)

print("test 3: three alerts together, they should play by importance")
manager.alert("low_battery", SYSTEM, "ar")
manager.alert("curb", HAZARD, "ar", side=1)
manager.alert("person", HAZARD, "ar", side=-1)
manager.wait_until_done()
print("done")