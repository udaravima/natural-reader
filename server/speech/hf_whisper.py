"""A Whisper model run as it is through Transformers, without converting it to CTranslate2. Slower, and it needs
`torch` and `transformers` in the backend process: this exists to compare a fine-tune against its converted self."""
from __future__ import annotations

import numpy as np
from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

from .audio import SAMPLE_RATE
from .base import Transcript
from .local_whisper import VAD_THRESHOLD, LocalWhisper


def speech_only(samples: np.ndarray) -> np.ndarray:
    """The recording with its silence cut out, by the same Silero VAD and threshold as the other path: Whisper
    invents text from silence. Empty when nobody spoke."""
    spoken = get_speech_timestamps(samples, VadOptions(threshold=VAD_THRESHOLD))
    return np.concatenate(collect_chunks(samples, spoken)[0]) if spoken else samples[:0]


class TransformersWhisper(LocalWhisper):
    def _open(self):
        from huggingface_hub import snapshot_download
        from transformers import pipeline

        path = snapshot_download(self.spec.repo, revision=self.spec.revision,
                                 allow_patterns=["*.json", "*.txt", "*.safetensors", "pytorch_model*.bin"])
        return pipeline("automatic-speech-recognition", model=path, device="cpu", chunk_length_s=30)

    def _run(self, samples, language: str | None, model) -> Transcript:
        import torch

        duration = samples.size / SAMPLE_RATE
        speech = speech_only(samples)
        if speech.size == 0:
            return Transcript("", language, duration)
        torch.set_num_threads(self.cpu_threads)   # process-wide, so set per run: Docling sets its own for a conversion
        kwargs = {"task": "transcribe", **({"language": language} if language else {})}
        out = model({"raw": speech, "sampling_rate": SAMPLE_RATE}, generate_kwargs=kwargs)
        return Transcript(out["text"].strip(), language, duration)
