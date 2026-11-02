"""Kokoro speaks phonemes, not text: which language espeak reads for a voice, and what reaches the model."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest

from server.tts_language import language_of, phonemes

REPO = Path(__file__).resolve().parents[2]
SINHALA = "ආයුබෝවන්, මම ඔබට උදව් කිරීමට සතුටු වෙමි."
MIXED = "මෙම Report එක PDF ලෙස save කරන්න."


class Tokenizer:
    def __init__(self, out):
        self.out, self.languages = out, []

    def phonemize(self, text, lang):
        self.languages.append(lang)
        return self.out


@pytest.fixture(scope="module")
def espeak():
    tokenizer = pytest.importorskip("kokoro_onnx.tokenizer")
    return tokenizer.Tokenizer()


@pytest.mark.parametrize("voice, language", [
    ("af_heart", "en-us"), ("am_santa", "en-us"), ("bf_emma", "en-gb"), ("bm_george", "en-gb"),
    ("hf_alpha", "hi"), ("hm_psi", "hi"), ("ef_dora", "es"), ("em_santa", "es"), ("ff_siwis", "fr-fr"),
    ("if_sara", "it"), ("im_nicola", "it"), ("pf_dora", "pt-br"), ("pm_alex", "pt-br"),
    ("jf_alpha", "ja"), ("jm_kumo", "ja"), ("zf_xiaobei", "cmn"),
])
def test_a_voice_reads_in_its_own_language(voice, language):
    assert language_of(voice) == language


def test_a_voice_nobody_has_seen_stays_on_american_english():
    assert language_of("xf_new") == "en-us"
    assert language_of("") == "en-us"


def test_the_voices_language_goes_to_espeak():
    tokenizer = Tokenizer("x")
    phonemes(tokenizer, "hola", "ef_dora")
    assert tokenizer.languages == ["es"]


def test_language_markers_are_removed():
    assert phonemes(Tokenizer("(si)mˈemə(enus) ɹᵻpˈɔːɹt (si)ˈekə(enus)."), "මෙම", "af_heart") == "mˈemə ɹᵻpˈɔːɹt ˈekə."


def test_real_brackets_stay():
    assert phonemes(Tokenizer("((en)ˈeɪ(hi))"), "(a)", "hf_alpha") == "(ˈeɪ)"
    assert phonemes(Tokenizer("(ðə kˈæt)"), "(the cat)", "af_heart") == "(ðə kˈæt)"


def test_sinhala_comes_out_the_same_through_any_voice_as_sinhala_itself(espeak):
    wanted = espeak.phonemize(SINHALA, "si")
    for voice in ("af_heart", "bf_emma", "hf_alpha", "ef_dora"):
        got = phonemes(espeak, SINHALA, voice)
        assert got == wanted and "(" not in got


def test_english_words_in_sinhala_stay_english(espeak):
    got = phonemes(espeak, MIXED, "af_heart")
    assert "(" not in got and "ɹᵻpˈɔːɹt" in got


def test_plain_english_is_exactly_what_espeak_said(espeak):
    text = "Hello there (and welcome), this is a test of 12 km at 5%."
    assert phonemes(espeak, text, "af_heart") == espeak.phonemize(text, "en-us")


def _ids(path: str) -> set[str]:
    return set(re.findall(r"(?:id: |otherVoice\()'([a-z]{2}_[a-z]+)'", (REPO / path).read_text()))


@pytest.mark.parametrize("path", ["src/constants.js", "extension/shared/voices.js"])
def test_every_voice_in_the_file_is_offered_and_nothing_else(path):
    if not (REPO / "voices-v1.0.bin").exists():
        pytest.skip("the voice file isn't here")
    assert _ids(path) == set(np.load(REPO / "voices-v1.0.bin").keys())


def test_every_voice_in_the_list_has_a_language_of_its_own():
    for voice in _ids("src/constants.js"):
        assert voice[0] in "abefhijpz", voice
