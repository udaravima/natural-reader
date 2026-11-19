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


def test_streamed_chirp_voices_are_chosen_by_name():
    from server.calls.voice import ChirpStreamVoice
    v = make_voice("chirp-stream:en-IN-Chirp3-HD-Kore", {})
    assert isinstance(v, ChirpStreamVoice) and v.language == "en-IN" and v.parallel == 2
    assert "streamed" in v.label
    with pytest.raises(SetupError, match="chirp-stream"):
        make_voice("chirp-stream:", {})


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeTTS:
    """Plays (seconds to wait, PCM bytes) pieces; records what was asked."""

    def __init__(self, clock, pieces, fail=None):
        self.clock, self.pieces, self.fail, self.asked = clock, pieces, fail, []

    async def streaming_synthesize(self, requests):
        async for r in requests:
            self.asked.append(r)

        async def responses():
            for wait, pcm in self.pieces:
                self.clock.t += wait
                if self.fail:
                    raise self.fail
                yield type("R", (), {"audio_content": pcm})()
        return responses()


async def test_a_streamed_chirp_voice_says_when_its_audio_could_start_and_returns_it_all():
    import io

    import soundfile as sf

    from server.calls.voice import ChirpStreamVoice
    clock = FakeClock()
    second = b"\0\0" * 24000   # one second of 24 kHz 16-bit silence
    fake = FakeTTS(clock, [(0.35, second[:9600]), (0.03, second[:11520]), (0.03, second)])
    v = ChirpStreamVoice("en-US-Chirp3-HD-Kore", client=fake, clock=clock)
    s = await v.speak("Check-in is from 2 pm.")
    assert s.first_audio_s == pytest.approx(0.35) and s.playable_s == pytest.approx(0.35)
    assert s.seconds == pytest.approx(0.41)
    assert s.audio_s == pytest.approx(0.2 + 0.24 + 1.0)
    assert sf.info(io.BytesIO(s.wav)).duration == pytest.approx(s.audio_s)
    config, text = fake.asked
    assert config.streaming_config.voice.name == "en-US-Chirp3-HD-Kore" and text.input.text == "Check-in is from 2 pm."
    assert s.error is None


async def test_a_streamed_chirp_voice_that_fails_reports_it_without_audio():
    from server.calls.voice import ChirpStreamVoice
    clock = FakeClock()
    v = ChirpStreamVoice("en-US-Chirp3-HD-Kore", client=FakeTTS(clock, [(0.2, b"")], fail=RuntimeError("quota")),
                         clock=clock)
    s = await v.speak("Hello there.")
    assert s.wav is None and "quota" in s.error and s.first_audio_s is None
    assert s.playable_s == s.seconds


def test_a_batch_voice_is_playable_only_once_all_of_it_is_made():
    from server.calls.voice import Spoken
    assert Spoken(None, 1.0, 0.7).playable_s == 0.7
