# Voice commands -> actions.
# The real actions (cancel fall, call ambulance, contact menu) come from other modules as callbacks.
# Calling an ambulance always needs a spoken "yes". Silence or anything unclear cancels it.

import time
import grammar
from output_manager import EMERGENCY, SYSTEM, INFO

CONFIRM_SECONDS = 8   # how long we wait for "yes" or "no"


class CommandHandler:
    def __init__(self, manager, lang="ar", on_cancel_fall=None,
                 on_call_ambulance=None, on_choose_contact=None):
        self.manager = manager
        self.lang = lang
        self.on_cancel_fall = on_cancel_fall
        self.on_call_ambulance = on_call_ambulance
        self.on_choose_contact = on_choose_contact
        self.fall_alarm_active = False
        self.last_alert = None             # (alert_id, level, side), used by "why"
        self.waiting_confirm_until = None  # time when the ambulance question expires

    def alert(self, alert_id, level, side=0.0):
        """Use this instead of manager.alert so we remember the last alert."""
        if alert_id == "fall_alarm":
            self.fall_alarm_active = True
        self.last_alert = (alert_id, level, side)
        self.manager.alert(alert_id, level, self.lang, side)

    def check_timeout(self):
        """Call this often. If nobody answered the ambulance question, cancel it."""
        if self.waiting_confirm_until and time.time() > self.waiting_confirm_until:
            self.waiting_confirm_until = None
            self.manager.alert("cancelled", SYSTEM, self.lang)

    def handle_heard(self, heard):
        self.check_timeout()
        result = grammar.match(heard)
        intent = result[1] if result else None

        # we asked "call an ambulance? yes or no": only yes continues, everything else cancels
        if self.waiting_confirm_until:
            self.waiting_confirm_until = None
            if intent == "yes":
                if self.on_call_ambulance:
                    self.on_call_ambulance()
                self.manager.alert("calling_ambulance", EMERGENCY, self.lang)
            else:
                self.manager.alert("cancelled", SYSTEM, self.lang)
            return intent

        if intent is None:
            self.manager.alert("not_understood", INFO, self.lang)
        elif intent == "im_ok":
            if self.fall_alarm_active:
                self.fall_alarm_active = False
                if self.on_cancel_fall:
                    self.on_cancel_fall()
                self.manager.alert("fall_cancelled", SYSTEM, self.lang)
        elif intent in ("why", "repeat"):
            # for now "why" repeats the last alert; reason code sentences come later
            if self.last_alert:
                alert_id, level, side = self.last_alert
                self.manager.alert(alert_id, level, self.lang, side)
        elif intent == "call_ambulance":
            self.manager.alert("confirm_ambulance", EMERGENCY, self.lang)
            # the countdown starts after the question has finished playing
            self.manager.wait_until_done()
            self.waiting_confirm_until = time.time() + CONFIRM_SECONDS
        elif intent in ("choose_contact", "help", "fell"):
            if self.on_choose_contact:
                self.on_choose_contact()
            self.manager.alert("choose_contact", EMERGENCY, self.lang)
        return intent