"""Choosing the benchmark's ears and voice from the command line (spec 2026-11-17-call-simulation)."""
import pytest

from server.calls.voice import GoogleEars, GoogleVoice, KokoroVoice, NoVoice, SetupError, TextEars, make_ears, \
    make_voice

RECOGNIZER = "projects/p/locations/asia-southeast1/recognizers/asr-si-en"


def test_ears_are_chosen_by_name():
    assert isinstance(make_ears("text"), TextEars)
    google = make_ears(f"google:{RECOGNIZER}", {})
    assert isinstance(google, GoogleEars) and google.label == "google asr-si-en"
    for bad in ("whisper", "google", "ears:x"):
        with pytest.raises(SetupError, match="--hear"):
            make_ears(bad, {})


def test_an_unknown_whisper_model_names_the_ones_there_are():
    with pytest.raises(SetupError, match="small"):
        make_ears("whisper:no-such-model", {})


def test_voices_are_chosen_by_name_with_the_deployments_defaults():
    assert isinstance(make_voice("none"), NoVoice)
    assert make_voice("kokoro").voice == "af_heart" and isinstance(make_voice("kokoro:bm_george"), KokoroVoice)
    v = make_voice("google", {"GOOGLE_TTS_MODEL": "gemini-2.5-flash-lite-preview-tts", "GOOGLE_TTS_VOICE": "Puck"})
    assert isinstance(v, GoogleVoice) and (v.model, v.voice) == ("gemini-2.5-flash-lite-preview-tts", "Puck")
    assert v.parallel == 2 and make_voice("kokoro").parallel == 1
    assert make_voice("google:gemini-2.5-flash-tts:Kore", {}).label == "google gemini-2.5-flash-tts Kore"
    with pytest.raises(SetupError, match="--voice"):
        make_voice("piper")


async def test_no_voice_takes_a_sentence_to_last_0_4_s_a_word():
    s = await NoVoice().speak("One moment while I check.")
    assert s.wav is None and s.audio_s == pytest.approx(2.0) and s.seconds == 0


async def test_text_ears_pass_the_script_through():
    heard = await TextEars().hear(None, "Book a room", "en")
    assert heard.text == "Book a room" and heard.seconds == 0


def test_openai_ears_default_to_openais_api_with_the_deployments_key():
    from server.calls.voice import OpenAIEars
    ears = make_ears("openai:whisper-1", {"INFERENCE_OPENAI_API_KEY": "k"})
    assert isinstance(ears, OpenAIEars) and ears.label == "openai whisper-1"
    assert ears.engine.where == "api.openai.com"
    other = make_ears("openai:whisper-small@https://asr.example.com/v1", {"SPEECH_API_KEY": "s"})
    assert other.engine.where == "asr.example.com" and other.label == "openai whisper-small"


def test_chirp_voices_take_their_language_from_the_voice_name():
    from server.calls.voice import ChirpVoice
    v = make_voice("chirp:en-IN-Chirp3-HD-Kore", {})
    assert isinstance(v, ChirpVoice) and v.language == "en-IN" and v.parallel == 2
    assert "not in the app" in v.label
    with pytest.raises(SetupError, match="chirp"):
        make_voice("chirp:", {})
