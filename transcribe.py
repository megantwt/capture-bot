"""Speech-to-text on your own laptop with Whisper (via faster-whisper). No API key needed."""

from __future__ import annotations

import os

import av
import numpy as np
from faster_whisper import WhisperModel

# "small" is a good balance of accuracy and speed on a laptop.
# Use "base" if it feels slow, or "medium" if it mishears names.
_model = WhisperModel(os.environ.get("WHISPER_MODEL", "small"), device="cpu", compute_type="int8")


def _load_audio(path: str) -> np.ndarray:
    # Decode the voice note to 16 kHz mono, which is what Whisper expects.
    chunks = []
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    with av.open(path) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray())
    return np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0


def transcribe(path: str, hints: list[str] | None = None) -> str:
    # Listing names you use often helps Whisper spell them correctly.
    initial_prompt = ("Names: " + ", ".join(hints[:80]) + ".") if hints else None
    segments, _ = _model.transcribe(_load_audio(path), initial_prompt=initial_prompt, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()
