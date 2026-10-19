import time
from audio_out import play_earcon

for name in ["info", "warning", "danger", "fault", "ok"]:
    print(name)
    play_earcon(name)
    time.sleep(0.7)