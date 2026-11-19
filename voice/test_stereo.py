import time
from audio_out import play_alert

for side, name in [(-1, "left"), (0, "center"), (1, "right")]:
    print(name)
    play_alert("obstacle", "ar", side)
    time.sleep(0.5)