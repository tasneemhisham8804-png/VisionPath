# Fixed alert phrases. Each one is generated once as an audio file.
# (id, arabic, english)
ALERTS = [
    ("obstacle", "عقبة أمامك", "Obstacle ahead"),
    ("curb", "رصيف", "Curb"),
    ("step_up", "درجة لأعلى", "Step up"),
    ("step_down", "درجة لأسفل", "Step down"),
    ("stairs", "درج", "Stairs"),
    ("pole", "عمود", "Pole"),
    ("overhead", "عائق في الأعلى", "Overhead obstacle"),
    ("hole", "حفرة", "Hole"),
    ("person", "شخص يقترب", "Person approaching"),
    ("vehicle", "سيارة", "Vehicle"),
    ("low_battery", "البطارية منخفضة", "Battery low"),
    ("weather", "الحساسات أقل دقة بسبب الطقس", "Sensors less reliable due to weather"),
    ("system_fault", "يوجد عطل في النظام", "System fault"),
    ("are_you_ok", "هل أنت بخير؟", "Are you OK?"),
    ("fall_alarm", "تم رصد سقوط، جاري طلب المساعدة", "Fall detected, calling for help"),
    ("fall_cancelled", "تم إلغاء الإنذار، الحمد لله على سلامتك", "Alarm cancelled, glad you are OK"),
    ("calling_ambulance", "جاري الاتصال بالإسعاف", "Calling an ambulance"),
    ("choose_contact", "من تريد أن أتصل به؟", "Who should I call?"),
    ("not_understood", "لم أفهم، حاول مرة أخرى", "I did not understand, please try again"),
    ("confirm_ambulance", "هل تريد الاتصال بالإسعاف؟ قل نعم أو لا", "Do you want to call an ambulance? Say yes or no"),
    ("cancelled", "تم الإلغاء", "Cancelled"),
    ("wellbeing_ok", "تمام، سعيد إنك بخير", "Great, glad you are OK"),
    ("nothing_ahead", "لا يوجد شيء أمامك", "Nothing ahead"),

]
from reasons import REASONS
for key in REASONS:
    ALERTS.append(("why_" + key, REASONS[key][0], REASONS[key][1]))

from first_run_phrases import PHRASES
for key in PHRASES:
    ALERTS.append(("fr_" + key, PHRASES[key][0], PHRASES[key][1]))