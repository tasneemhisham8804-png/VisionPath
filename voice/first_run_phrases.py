# First run sentences (my proposal, they change when the team sends the real script).
# This file imports nothing, so alerts.py can use it safely.

# key -> (arabic, english). Each one becomes an alert with the id "fr_" + key
PHRASES = {
    "welcome":      ("أهلاً بك. سأرشدك لتشغيل الجهاز لأول مرة. قل ابدأ للمتابعة",
                     "Welcome. I will guide you through the first setup. Say start to continue"),
    "headphones":   ("ضع السماعات في أذنيك، ثم قل ابدأ",
                     "Put the headphones on your ears, then say start"),
    "hotspot":      ("شغل نقطة الاتصال في هاتفك. سأنتظر",
                     "Turn on the hotspot on your phone. I will wait"),
    "hotspot_ok":   ("تم الاتصال بالإنترنت",
                     "Connected to the internet"),
    "hotspot_fail": ("لم يتم الاتصال. تأكد أن نقطة الاتصال تعمل",
                     "Could not connect. Make sure the hotspot is on"),
    "try":          ("جرب الآن، قل إيه قدامي",
                     "Now try it. Say: what is ahead"),
    "done":         ("الجهاز جاهز. قل ابدأ في أي وقت للملاحة",
                     "The device is ready. Say start at any time to begin navigation"),
}