"""The phonemes Kokoro speaks, and which language they're read in.

Kokoro never sees text. It speaks phonemes, sound symbols, and espeak-ng makes those from the text. So the language
is espeak's job, and the first letter of a voice's name says which one it's for. Asked for the wrong one, espeak
reads Spanish as if it were English: the sounds are English and no voice can save them.

espeak also notices a script that isn't the language it was asked for and changes language by itself. That's how
every voice can read Sinhala and Tamil, which Kokoro has no voices for, and why English words stay English in the
middle of them. Each change leaves a marker such as (si) or (enus) in the output, in characters Kokoro accepts, so
it would speak them: on a Sinhala sentence that added 2 s of noise to 7 s of speech. They're taken out here.

A bare (la) or (me) in Portuguese looks like a marker, and goes too. No English word does, across 2,732 tried.
"""
from __future__ import annotations

import re

DEFAULT_LANGUAGE = "en-us"
_LANGUAGES = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br",
              "z": "cmn"}
_MARKER = re.compile(r"\([a-z]{2,6}\)")


def language_of(voice: str) -> str:
    return _LANGUAGES.get(voice[:1], DEFAULT_LANGUAGE)


def phonemes(tokenizer, text: str, voice: str) -> str:
    return _MARKER.sub("", tokenizer.phonemize(text, language_of(voice)))
