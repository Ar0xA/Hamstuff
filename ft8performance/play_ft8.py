##Play a 48k hz .wav recording as ft8 sound input for decoding.
##If DT is off, you can tweak this by playing with offset, currently at 1.5

import time
import datetime
import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
except ImportError:
    print("Missing libraries. Please run: pip install sounddevice soundfile")
    exit(1)


def shift_audio(data, fs, offset):
    """
    Shift audio in memory.

    Positive offset  = audio starts earlier
    Negative offset  = audio starts later
    """

    samples = int(abs(offset) * fs)

    if offset > 0:
        # Remove samples from the beginning
        if samples >= len(data):
            return np.zeros_like(data)

        shifted = data[samples:]

        padding = np.zeros(
            (samples,) if data.ndim == 1 else (samples, data.shape[1]),
            dtype=data.dtype
        )

        return np.concatenate((shifted, padding))

    elif offset < 0:
        # Add silence at the beginning
        padding = np.zeros(
            (samples,) if data.ndim == 1 else (samples, data.shape[1]),
            dtype=data.dtype
        )

        shifted = np.concatenate((padding, data))

        return shifted[:len(data)]

    return data


def play_ft8_recording(filename="recording.wav",
                       wait_time=30,
                       offset=0.0):

    print(f"Loading '{filename}'...")

    try:
        data, fs = sf.read(filename)
    except Exception as e:
        print(f"Error loading file: {e}")
        return

    print(f"Sample rate: {fs} Hz")
    print(f"Applying audio offset: {offset:+.3f} seconds")

    data = shift_audio(data, fs, offset)

    now = datetime.datetime.now()
    ready_time = now + datetime.timedelta(seconds=wait_time)

    remainder = ready_time.second % 15
    seconds_to_next_boundary = (15 - remainder) % 15

    target_time = ready_time + datetime.timedelta(
        seconds=seconds_to_next_boundary
    )

    target_time = target_time.replace(microsecond=0)

    delay = (target_time - now).total_seconds()

    print(f"Playback slot: {target_time.strftime('%H:%M:%S')}")

    if delay > 0.1:
        time.sleep(delay - 0.1)

    while datetime.datetime.now() < target_time:
        pass

    print(
        f">>> PLAYING {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}"
    )

    sd.play(data, fs)
    sd.wait()

    print("Done.")


if __name__ == "__main__":

    # Change this value for testing
    # +1.5 means make the FT8 audio arrive 1.5s earlier
    play_ft8_recording(
        "recording.wav",
        wait_time=28.7,
        offset=1.5
    )
