"""
VisionPath — Weeks 1-2 deliverable
Mariam Mohamed Mahmoud — Cloud, AI Pipeline and Indoor Localization

Task (per "Responsibilities by Team Member", Revision 5.0, 10 Oct 2026):
  "... IMU step counting verified on a 50 step walk."

Hardware: BMI270 IMU (per Malak Emad's wiring, confirmed through depthai at +-16g)
Library:  numpy + scipy.signal for peak detection on acceleration magnitude

------------------------------------------------------------------------
WHAT "STEP COUNTING" MEANS HERE
------------------------------------------------------------------------
A phone/wearable pedometer works by watching the magnitude of the total
acceleration vector (combining X, Y, Z). Standing still, that magnitude
sits flat around 1g (just gravity). Each walking step produces a distinct
peak in that signal (the foot impact / body bounce), followed by a dip.
So "counting steps" = counting peaks in the acceleration-magnitude signal
that are spaced out like real walking (not noise, not a single jolt).

This matters for dead reckoning (Week 6): step count x stride length
gives you a rough distance traveled when GPS is unavailable (indoors).

------------------------------------------------------------------------
HOW TO SWITCH THIS FROM MOCK DATA TO THE REAL BMI270
------------------------------------------------------------------------
This file runs in MOCK mode by default — generates a realistic 50-step
walking signal with noise, so we can build and verify the detection logic
right now. Once the BMI270 is wired and readable (Malak Emad's task):

  1. Set USE_MOCK_IMU = False below.
  2. Implement RealIMUReader.read_samples() to pull live accel samples —
     most likely over I2C via smbus2, or through the OAK-D's onboard IMU
     passthrough if that's how Malak Emad exposes it (per the Role Guide,
     page 23: "IMU confirmed through depthai").
  3. Everything downstream (peak detection, step counting, verification)
     stays exactly the same — it only cares about a stream of
     (timestamp, accel_magnitude) samples, not where they came from.
------------------------------------------------------------------------
"""

import numpy as np
from scipy.signal import find_peaks, butter, sosfiltfilt
from dataclasses import dataclass
from typing import List, Tuple

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

USE_MOCK_IMU = True        # set False once the BMI270 is wired and readable
SAMPLE_RATE_HZ = 50         # typical accelerometer sampling rate
TRUE_STEP_COUNT = 50        # ground truth for the verification walk


# ---------------------------------------------------------------------------
# 1. MOCK IMU DATA — simulates a realistic 50-step walk
# ---------------------------------------------------------------------------

def generate_mock_walk(num_steps: int = 50, sample_rate_hz: int = 50,
                        step_duration_s: float = 0.55, noise_std: float = 0.08,
                        seed: int = 42) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generates acceleration-magnitude samples for a simulated walk.
    Each step is modeled as a bump above the 1g gravity baseline, spaced
    about step_duration_s apart (~0.5-0.6s is a normal walking cadence),
    with realistic sensor noise added on top.

    Returns (timestamps, magnitudes).
    """
    rng = np.random.default_rng(seed)
    total_duration_s = num_steps * step_duration_s
    t = np.arange(0, total_duration_s, 1 / sample_rate_hz)

    # Baseline: standing still would read ~1g constantly.
    signal = np.ones_like(t)

    # Add one bump per step at roughly even intervals (slight timing jitter
    # per step, since real walking isn't a perfect metronome).
    for i in range(num_steps):
        step_center = i * step_duration_s + rng.normal(0, 0.03)
        bump_width = 0.12
        # Gaussian-shaped bump simulates the impact/push-off acceleration spike
        signal += 0.6 * np.exp(-0.5 * ((t - step_center) / bump_width) ** 2)

    # Sensor noise (vibration, imperfect mounting, electrical noise)
    signal += rng.normal(0, noise_std, size=signal.shape)

    return t, signal


class MockIMUReader:
    """Stand-in for the real BMI270. Produces the same (timestamps,
    magnitudes) shape a real reader would, so step-detection logic never
    needs to know which one it's talking to."""

    def read_samples(self) -> Tuple[np.ndarray, np.ndarray]:
        return generate_mock_walk(num_steps=TRUE_STEP_COUNT, sample_rate_hz=SAMPLE_RATE_HZ)


# ---------------------------------------------------------------------------
# 2. REAL IMU READER (stub — fill in once BMI270 is wired)
# ---------------------------------------------------------------------------

class RealIMUReader:
    """
    Placeholder for the real BMI270 reader. Implement once Malak Emad
    confirms how the IMU is exposed (I2C address, or passthrough via the
    OAK-D's depthai SDK per the Role Guide).

    Must return the same shape as MockIMUReader: (timestamps, magnitudes),
    where magnitude = sqrt(ax^2 + ay^2 + az^2) in units of g.
    """

    def read_samples(self) -> Tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError(
            "Wire up real BMI270 reads here once hardware is available — "
            "likely via smbus2 (I2C) or depthai's IMU passthrough."
        )


# ---------------------------------------------------------------------------
# 3. STEP DETECTOR — peak detection on acceleration magnitude
# ---------------------------------------------------------------------------

@dataclass
class StepDetectionResult:
    step_count: int
    step_timestamps: List[float]
    cadence_steps_per_min: float


def smooth_signal(magnitudes: np.ndarray, sample_rate_hz: int, cutoff_hz: float = 3.0) -> np.ndarray:
    """
    Low-pass filters the raw accelerometer magnitude before peak detection.
    Real sensor noise (vibration, electrical) has much higher frequency
    content than actual footsteps (walking cadence tops out around 3 Hz,
    i.e. 180 steps/min), so filtering above that cleanly removes noise
    spikes without blurring out real steps. Without this, isolated noise
    samples can randomly exceed the peak threshold and get miscounted as
    extra steps — exactly the bug that caused the first version of this
    detector to overcount by 50%.
    """
    nyquist = sample_rate_hz / 2
    normal_cutoff = cutoff_hz / nyquist
    # output="sos" (second-order sections) returns a single array instead of
    # a (b, a) tuple — numerically more stable than the "ba" form, and also
    # sidesteps a Pylance/scipy-stubs overload-resolution quirk where the
    # "ba" tuple's exact size can't always be inferred statically.
    sos = butter(N=2, Wn=normal_cutoff, btype="low", output="sos")
    return sosfiltfilt(sos, magnitudes)


def detect_steps(timestamps: np.ndarray, magnitudes: np.ndarray,
                  sample_rate_hz: int = SAMPLE_RATE_HZ) -> StepDetectionResult:
    """
    Detects steps as peaks in the (smoothed) acceleration-magnitude signal.

    - height: a peak must rise at least this far above the 1g baseline to
      count as a step, not noise.
    - distance: minimum samples between two peaks, so we don't double-count
      a single step's vibration as two steps. At 50Hz, 0.3s between peaks
      means we can't detect cadences faster than ~200 steps/min, comfortably
      above normal walking pace (~100-130 steps/min).
    """
    smoothed = smooth_signal(magnitudes, sample_rate_hz)

    min_peak_height = 1.08  # g — lower threshold is safe now that noise is filtered out
    min_distance_samples = int(0.3 * sample_rate_hz)

    peak_indices, _ = find_peaks(smoothed, height=min_peak_height, distance=min_distance_samples)
    step_times = timestamps[peak_indices].tolist()

    if len(step_times) >= 2:
        total_time = step_times[-1] - step_times[0]
        cadence = (len(step_times) - 1) / total_time * 60 if total_time > 0 else 0.0
    else:
        cadence = 0.0

    return StepDetectionResult(
        step_count=len(step_times),
        step_timestamps=step_times,
        cadence_steps_per_min=round(cadence, 1),
    )


# ---------------------------------------------------------------------------
# 4. VERIFICATION — "IMU step counting verified on a 50 step walk"
# ---------------------------------------------------------------------------

def verify_against_known_walk(true_step_count: int = TRUE_STEP_COUNT):
    reader = MockIMUReader() if USE_MOCK_IMU else RealIMUReader()
    print(f"Reading IMU samples — mode: {'MOCK' if USE_MOCK_IMU else 'REAL (BMI270)'}\n")

    timestamps, magnitudes = reader.read_samples()
    result = detect_steps(timestamps, magnitudes)

    error = result.step_count - true_step_count
    error_pct = abs(error) / true_step_count * 100

    print("=" * 60)
    print("STEP COUNTING VERIFICATION")
    print("=" * 60)
    print(f"Ground truth steps walked:   {true_step_count}")
    print(f"Detected steps:              {result.step_count}")
    print(f"Error:                       {error:+d} steps ({error_pct:.1f}%)")
    print(f"Estimated cadence:           {result.cadence_steps_per_min} steps/min")
    print(f"Walk duration:               {timestamps[-1]:.1f} s")
    print("=" * 60)

    if error_pct <= 5:
        print("PASS — within 5% of ground truth (matches the drift/accuracy")
        print("       bar used elsewhere in the project, e.g. Week 6's 5% drift target).")
    else:
        print("NEEDS TUNING — detection is off by more than 5%. Try adjusting")
        print("       min_peak_height or min_distance_samples in detect_steps().")

    return result


if __name__ == "__main__":
    verify_against_known_walk()
