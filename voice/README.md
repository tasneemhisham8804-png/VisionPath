\# VisionPath - Voice Commands (Week 1 Prototype)



Voice command prototype for the VisionPath assistive navigation system.

Recognizes three commands in Arabic and English: start / stop / repeat.



\## How to run

&#x20;   pip install faster-whisper sounddevice scipy gTTS pygame

&#x20;   python voice\_week1.py



Press Enter, then speak within 3 seconds. Requires a microphone and internet (for gTTS).



\## Stack

\- Speech-to-text: faster-whisper (model: small, int8, CPU). One model handles Arabic and English.

\- Text-to-speech: gTTS (Arabic + English).

\- Why: fast to set up and supports both languages. Piper is planned later for offline use on the Raspberry Pi 5.



\## Results (first test)

16 attempts in total. 3 of them were random speech (no command spoken), and the system correctly returned "unknown", so they are not counted as failures.



| Language | Valid attempts | Correct | Accuracy |

|----------|----------------|---------|----------|

| Arabic   | 9              | 9       | 100%     |

| English  | 4              | 4       | 100%     |

| Total    | 13             | 13      | 100%     |



Time per command: about 5 s (Arabic) and 6.5 s (English), including 3 s of recording.

This is a small sample. A larger test is planned.



\## Known issues

\- Small test sample (only 4 valid English attempts).

\- English is slower because the script tries Arabic first, then English.

\- The "small" model needed a lot of RAM; it only loaded after closing other programs.

\- gTTS needs internet.



\## Next steps

\- Week 2: collect Egyptian Arabic voice samples.

\- Week 3: Piper for offline speech and the emergency alert message.

\- Week 4: tune recognition for Egyptian accents and expand the test set.



\## Week 2 - Egyptian Arabic voice samples

\- Script: `record\_samples.py` records a fixed list of 20 phrases (commands, help, navigation, and longer sentences) with a consent check, a speaker ID (no real names), and a `metadata.csv` file.

\- Collected so far: 1 speaker (s01), 20 recordings, 5 seconds each, 16 kHz mono.

\- Status: first speaker only. More volunteers will be recorded to make the set more varied (gender, age, accent).

\- Audio files are not in the repo (excluded in `.gitignore`); they are shared separately.

\- Next: use these samples in week 4 to measure and tune recognition for Egyptian accents.

## Next steps

\- Week 3: Piper for offline speech and the emergency alert message.

\- Week 4: tune recognition for Egyptian accents and expand the test set.

