\# Voice module interface (proposal, Mostafa)



\## What the voice module needs from the other modules



Call:  manager.alert(alert\_id, level, lang, side)



\- alert\_id : one of the ids in voice/alerts.py

\- level    : 0 EMERGENCY, 1 HAZARD, 2 SYSTEM, 3 INFO

\- lang     : "ar" or "en"

\- side     : -1.0 (left) to 1.0 (right), 0.0 = center



A higher level interrupts a lower one that is playing.



\## Proposed reason codes (for the "Why?" command)



Tasneem / Mariam: please confirm, rename or add. One spoken sentence per code.



| code            | meaning                      | who sends it |

|-----------------|------------------------------|--------------|

| obstacle        | something blocks the path    | vision       |

| curb            | curb ahead                   | vision       |

| step\_up         | step up ahead                | vision       |

| step\_down       | step down ahead              | vision       |

| stairs          | stairs ahead                 | vision       |

| pole            | pole ahead                   | vision       |

| overhead        | object at head height        | vision       |

| hole            | hole or gap in the ground    | vision       |

| person          | person close                 | vision       |

| vehicle         | vehicle close                | vision       |

| low\_battery     | battery is low               | system       |

| system\_fault    | a sensor or module failed    | system       |

| fall\_alarm      | fall detected                | sensors      |



\## Open questions

1\. Does vision send distance (meters) with the code? If yes I can say "pole, 2 meters".

2\. Who sends fall\_alarm and the "I am OK" cancel signal?

3\. CPU budget for voice on the Pi: what percent can I use?

