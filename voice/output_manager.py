# Output manager: one channel at a time.
# Each alert plays in this order: vibration, earcon, speech.
# A more important alert interrupts a less important one.

import threading
import time
import audio_out

# lower number = more important
EMERGENCY = 0   # fall alarm, "Are you OK?"
HAZARD = 1      # obstacle, curb, stairs...
SYSTEM = 2      # battery, weather, fault
INFO = 3        # normal navigation speech

EARCON_FOR_LEVEL = {
    EMERGENCY: "danger",
    HAZARD: "warning",
    SYSTEM: "fault",
    INFO: "info",
}


class OutputManager:
    def __init__(self, vibrate=None):
        # vibrate is a function(level, side) that comes from the haptic module (Baraa)
        self.vibrate = vibrate
        self.queue = []
        self.lock = threading.Lock()
        self.current_level = None
        self.stop_current = threading.Event()
        self.player = audio_out.get_player()
        self.running = True
        threading.Thread(target=self.worker, daemon=True).start()

    def alert(self, alert_id, level, lang="ar", side=0.0):
        with self.lock:
            self.queue.append((level, alert_id, lang, side))
            # a more important alert cuts the one that is playing now
            if self.current_level is not None and level < self.current_level:
                self.stop_current.set()
                self.player.stop()

    def worker(self):
        while self.running:
            item = None
            with self.lock:
                if self.queue:
                    self.queue.sort(key=lambda q: q[0])
                    item = self.queue.pop(0)
                    self.current_level = item[0]
                    self.stop_current.clear()
            if item is None:
                time.sleep(0.01)
                continue

            level, alert_id, lang, side = item
            if self.vibrate:
                self.vibrate(level, side)
            if not self.stop_current.is_set():
                audio_out.play_earcon(EARCON_FOR_LEVEL[level], side)
            if not self.stop_current.is_set():
                audio_out.play_alert(alert_id, lang, side)

            with self.lock:
                self.current_level = None

    def wait_until_done(self):
        while True:
            with self.lock:
                if not self.queue and self.current_level is None:
                    return
            time.sleep(0.05)