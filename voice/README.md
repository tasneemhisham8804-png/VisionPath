# VisionPath - Voice Commands (Week 1 Prototype)



Voice command prototype for the VisionPath assistive navigation system.

Recognizes three commands in Arabic and English: start / stop / repeat.



## How to run

    pip install faster-whisper sounddevice scipy gTTS pygame

    python voice_week1.py



Press Enter, then speak within 3 seconds. Requires a microphone and internet (for gTTS).



## Stack

- Speech-to-text: faster-whisper (model: small, int8, CPU). One model handles Arabic and English.

- Text-to-speech: gTTS (Arabic + English).

- Why: fast to set up and supports both languages. Piper is planned later for offline use on the Raspberry Pi 5.



## Results (first test)

16 attempts in total. 3 of them were random speech (no command spoken), and the system correctly returned "unknown", so they are not counted as failures.



| Language | Valid attempts | Correct | Accuracy |

|----------|----------------|---------|----------|

| Arabic   | 9              | 9       | 100%     |

| English  | 4              | 4       | 100%     |

| Total    | 13             | 13      | 100%     |



Time per command: about 5 s (Arabic) and 6.5 s (English), including 3 s of recording.

This is a small sample. A larger test is planned.



## Known issues

- Small test sample (only 4 valid English attempts).

- English is slower because the script tries Arabic first, then English.

- The "small" model needed a lot of RAM; it only loaded after closing other programs.

- gTTS needs internet.





## Week 3 - Alert audio, earcons, stereo output, recognition measurements

- `alerts.py`: 15 fixed alert phrases (Arabic and English), including "Are you OK?" and the fall alarm.

- `generate_alerts.py`: generates one wav file per phrase (needs internet once, gTTS). Playback is fully local. These are TTS voices, not human recordings.

- `generate_earcons.py`: 5 short tones, all under 200 ms (100, 160, 155, 180, 150 ms).

- `audio_out.py`: stereo playback (side from -1 left to 1 right) with one output stream kept open, because the laptop sound device swallowed short sounds when a stream was opened for each sound.

- `compare_configs.py`, `measure_noise.py`: recognition measurements on the s01 samples.



### Recognition results (faster-whisper small, int8, laptop CPU)

Test set: 1 speaker (s01), 20 recordings. The hint contains the 20 expected phrases (closed vocabulary).



| Setting | Correct |

|---|---|

| no hint | 0 / 20 |

| hint | 13 / 20 |

| hint + VAD | 14 / 20 (70%) |



With added noise (hint + VAD): clean 14/20, 20 dB 14/20, 10 dB 10/20, 5 dB 10/20.

Average time per 5 s recording: about 2 to 4 s on the laptop.



### Limits

- Noise is synthetic white noise, not recorded street noise.

- One speaker and 20 phrases only, so the numbers are rough. The target of 90% (R14) is not reached yet; the fixed command grammar is planned for week 4.

- Timing was measured on the laptop, not on the Raspberry Pi.

- Stereo was tested on laptop headphones only, not on the USB sound card and wired headphones.



## Week 2 - Egyptian Arabic voice samples

- Script: `record_samples.py` records a fixed list of 20 phrases (commands, help, navigation, and longer sentences) with a consent check, a speaker ID (no real names), and a `metadata.csv` file.

- Collected so far: 1 speaker (s01), 20 recordings, 5 seconds each, 16 kHz mono.

- Status: first speaker only. More volunteers will be recorded to make the set more varied (gender, age, accent).

- Audio files are not in the repo (excluded in `.gitignore`); they are shared separately.

- Next: use these samples in week 4 to measure and tune recognition for Egyptian accents.
