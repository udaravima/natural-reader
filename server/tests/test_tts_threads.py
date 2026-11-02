"""How many threads Kokoro gets: more isn't faster (server/tts_threads.py)."""
from server.tts_threads import tts_threads


def test_a_few_threads_by_default_on_a_big_machine():
    assert tts_threads({}, cores=32) == 6


def test_never_more_than_the_machine_has():
    assert tts_threads({}, cores=4) == 4


def test_the_environment_wins():
    assert tts_threads({"TTS_CPU_THREADS": "12"}, cores=32) == 12


def test_a_bad_value_keeps_the_default(caplog):
    assert tts_threads({"TTS_CPU_THREADS": "many"}, cores=32) == 6
    assert "TTS_CPU_THREADS" in caplog.text
