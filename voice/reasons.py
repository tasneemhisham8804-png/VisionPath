# Why? sentences: one explanation for each alert id (Arabic, English)
# These are my proposal. They change when Tasneem sends the final reason codes.

REASONS = {
    "obstacle":     ("حذرتك لأن فيه عائق في طريقك", "I warned you because there is an obstacle in your way"),
    "curb":         ("حذرتك لأن فيه رصيف قدامك", "I warned you because there is a curb ahead"),
    "step_up":      ("حذرتك لأن فيه درجة طالعة قدامك", "I warned you because there is a step up ahead"),
    "step_down":    ("حذرتك لأن فيه درجة نازلة قدامك", "I warned you because there is a step down ahead"),
    "stairs":       ("حذرتك لأن فيه سلم قدامك", "I warned you because there are stairs ahead"),
    "pole":         ("حذرتك لأن فيه عمود قدامك", "I warned you because there is a pole ahead"),
    "overhead":     ("حذرتك لأن فيه حاجة على مستوى راسك", "I warned you because there is something at head height"),
    "hole":         ("حذرتك لأن فيه حفرة في الأرض", "I warned you because there is a hole in the ground"),
    "person":       ("حذرتك لأن فيه شخص قريب منك", "I warned you because a person is close"),
    "vehicle":      ("حذرتك لأن فيه عربية قريبة منك", "I warned you because a vehicle is close"),
    "low_battery":  ("قلتلك لأن البطارية قربت تخلص", "I told you because the battery is almost empty"),
    "weather":      ("قلتلك لأن الجو بيقلل دقة الحساسات", "I told you because the weather reduces sensor accuracy"),
    "system_fault": ("قلتلك لأن جزء من الجهاز مش شغال صح", "I told you because a part of the device is not working properly"),
    "fall_alarm":   ("نبهتك لأن الجهاز حس إنك وقعت", "I alerted you because the device sensed a fall"),
    "are_you_ok":   ("سألتك لأن الجهاز حس بحركة مش عادية", "I asked because the device sensed an unusual movement"),
}