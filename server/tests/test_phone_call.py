"""The call session (server/phone/call.py): Talk's listen, hear, answer and interrupt loop, run for one phone call.

Everything runs on a virtual clock: an event loop whose time jumps to the next timer whenever nothing is ready to
run, so a minute of call takes milliseconds and every timer fires exactly when due. The caller is scripted (marker
frames stand in for the VAD, except in the end-to-end test), the recognizer, the chat engine and the voice are fakes,
and every frame the pacer sends is recorded. Each clip and each sentence is a constant tone, so the frames tell what
the caller heard, and when."""
import asyncio
import dataclasses
import io
import logging
import selectors
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from scipy.signal import resample_poly

from server.chat.store import Heard, TurnClaim
from server.phone.call import CallDeps, CallSession, Hearing, Upsampler
from server.phone.config import PhoneConfig
from server.phone.media import Audio, Digit, HungUp
from server.phone.segmenter import Misfire, Segmenter, SpeechEnded, SpeechStarted
from server.phone.store import Line, LineVoice
from server.speech import window
from server.speech.base import Transcript

FIXTURES = Path(__file__).parent / "fixtures" / "phone"
SETTINGS = window.settings({})        # the defaults: silence_ms 700, first_clause_chars 30, ...


# --- a clock that jumps ---

class _Jump(selectors.DefaultSelector):
    """Never waits for real: when nothing is ready to run, the loop's clock jumps to its next timer."""
    loop: "VirtualLoop"

    def select(self, timeout=None):
        ready = super().select(0)
        if ready or timeout == 0:
            return ready
        if timeout is None:
            raise RuntimeError("stuck: every task is waiting and no timer is set")
        self.loop.now += timeout
        if self.loop.now > 3600:
            raise RuntimeError("the call ran past an hour of virtual time")
        return []


class VirtualLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self.now = 0.0
        selector = _Jump()
        super().__init__(selector)
        selector.loop = self

    def time(self) -> float:
        return self.now


def run_virtual(main):
    loop = VirtualLoop()
    try:
        return loop.run_until_complete(main)
    finally:
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


# --- the line, its clips, and the fakes ---

LINE = Line(id="0b6f7a1c-3c55-4a39-9d7e-2f1a4c8e9b10", name="Front desk", enabled=True, profile_id="profile-1",
            project_id="project-1", model_id="ollama:test-model", voice=LineVoice("kokoro", "af_heart", None),
            language="en", greeting="Hello, you have reached the library. How can I help?", max_call_s=600,
            max_calls=2, service_user_id="line-user-1", daily_tokens=None)

CLIP_TONES = {"greeting": 1, "busy": 2, "closed": 3, "goodbye": 4, "still": 5, "sorry": 6, "filler0": 7,
              "filler1": 8, "checking": 9}


def tone(value: int, seconds: float) -> bytes:
    """`seconds` of 8 kHz s16le whose every sample is `value`: a sound the frames can be traced back to."""
    return np.full(int(round(seconds * 8000)), value, dtype="<i2").tobytes()


CLIPS = {"greeting": [tone(1, 0.4)], "busy": [tone(2, 0.4)], "closed": [tone(3, 0.4)], "goodbye": [tone(4, 1.0)],
         "still": [tone(5, 0.4)], "sorry": [tone(6, 0.4)], "filler": [tone(7, 0.4), tone(8, 0.4)],
         "checking": [tone(9, 0.4)]}

START, END, MISFIRE = Audio(b"<start>"), Audio(b"<end>"), Audio(b"<misfire>")


def speech(start: float, end: float) -> list:
    """The caller talks from `start`; the VAD calls it over (after its silence) at `end`."""
    return [(start, START), (end, END)]


class ScriptedHearing:
    """Stands in for the VAD and the segmenter: marker frames become their events. A clip is 1 s long."""

    def __init__(self, settings: dict, max_run_s: float | None = None) -> None:
        pass

    def push(self, pcm: bytes) -> list:
        return {b"<start>": [SpeechStarted()], b"<end>": [SpeechEnded(np.zeros(16000, dtype=np.float32))],
                b"<misfire>": [Misfire()]}.get(pcm, [])


class ScoredHearing:
    """Stands in for Silero only: each Audio(b"p0.40") is one 32 ms window scoring 0.40, through the real segmenter
    with the cap the session hands it."""

    def __init__(self, settings: dict, max_run_s: float | None = None) -> None:
        self._segmenter = Segmenter(settings, max_run_s=max_run_s)

    def push(self, pcm: bytes) -> list:
        return self._segmenter.step(float(pcm[1:]), np.zeros(512, dtype=np.float32))


def scores(start: float, *spans: tuple[float, float]) -> list:
    """The caller's line from `start` as window scores: (score, seconds) spans, a window every 32 ms."""
    out, at = [], start
    for score, seconds in spans:
        for _ in range(round(seconds / 0.032)):
            out.append((round(at, 4), Audio(b"p%.2f" % score)))
            at += 0.032
    return out


class FakeLine:
    """The caller's end of the call: `script` is (time, event) pairs. Records every frame sent, with its time. Like
    the AudioSocket adapter, the call stays up after the script until someone hangs up, and a send after the end
    raises ConnectionError."""
    sample_rate = 8000
    line_id = LINE.id

    def __init__(self, script: list) -> None:
        self.script = sorted(script, key=lambda item: item[0])
        self.frames: list[tuple[float, bytes]] = []
        self.hung_up_at: float | None = None
        self.gone_at: float | None = None
        self.late_sends = 0               # frames offered once the hang-up had begun: audio after the 0x00
        self._ended = asyncio.Event()

    async def events(self):
        loop = asyncio.get_running_loop()
        for at, event in self.script:
            await asyncio.sleep(max(0.0, at - loop.time()))
            if self.hung_up_at is not None:
                break
            yield event
            if isinstance(event, HungUp):
                self.gone_at = loop.time()
                return
        await self._ended.wait()
        yield HungUp("caller")

    async def send(self, frame: bytes) -> None:
        if self.hung_up_at is not None:
            self.late_sends += 1
        if self.gone_at is not None or self.hung_up_at is not None:
            raise ConnectionError("the call has ended")
        assert len(frame) == 320
        self.frames.append((asyncio.get_running_loop().time(), frame))

    async def hang_up(self) -> None:
        """Takes a moment, as the real one does (it waits for the 0x00 to drain)."""
        self.hung_up_at = asyncio.get_running_loop().time()
        self._ended.set()
        await asyncio.sleep(0.05)


FINISH = {"type": "finish"}
TOOL = {"type": "tool-input-available", "toolCallId": "c1", "toolName": "search_documents", "input": {}}
TOOL_DONE = {"type": "tool-output-available", "toolCallId": "c1", "output": "found"}


def says(*deltas: str) -> list:
    """One step's text, as run_turn sends it."""
    return [{"type": "text-start", "id": "t"}, *({"type": "text-delta", "id": "t", "delta": d} for d in deltas),
            {"type": "text-end", "id": "t"}]


def fails(code: str) -> dict:
    return {"type": "error", "code": code, "message": "The model provider returned an error."}


class FakeChat:
    """`begin` and `run`. Each `run` plays the next script: "start", then its events; a number is a pause in seconds
    (the model thinking, or a tool running). Closing the generator runs its `finally`, which, like run_turn's
    `_finalize`, takes a moment (`finalize_s`) to save the reply and give the claim back."""

    def __init__(self, scripts, *, begin_s: float = 0.0, finalize_s: float = 0.05) -> None:
        self.scripts = list(scripts)
        self.begin_s = begin_s
        self.finalize_s = finalize_s
        self.begins: list[dict] = []
        self.begun_at: list[float] = []
        self.requests: list = []
        self.turns: list[dict] = []

    async def begin(self, **kw) -> TurnClaim:
        self.begins.append(kw)
        self.begun_at.append(asyncio.get_running_loop().time())
        n = len(self.begins)
        await asyncio.sleep(self.begin_s)
        return TurnClaim(kw["session_id"], f"u{n}", f"a{n}", n == 1)

    def run(self, req, claim):
        self.requests.append(req)
        return self._events(self.scripts.pop(0) if self.scripts else [FINISH], claim)

    async def _events(self, script, claim):
        loop = asyncio.get_running_loop()
        record = {"message": claim.assistant_message_id, "pulled": 0, "finalized_at": None}
        self.turns.append(record)
        try:
            for item in [{"type": "start", "messageId": claim.assistant_message_id}, *script]:
                if isinstance(item, (int, float)):
                    await asyncio.sleep(item)
                else:
                    record["pulled"] += 1
                    yield item
        finally:
            await asyncio.sleep(self.finalize_s)
            record["finalized_at"] = loop.time()


class FakeEars:
    """The recognizer: each clip takes `seconds` and gets the next result (a Transcript, or an exception raised)."""

    def __init__(self, results, seconds: float = 0.3) -> None:
        self.results = list(results)
        self.seconds = seconds
        self.calls: list[dict] = []

    async def __call__(self, wav: bytes, key: str) -> Transcript:
        self.calls.append({"at": asyncio.get_running_loop().time(), "key": key, "wav": wav})
        await asyncio.sleep(self.seconds)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FakeVoice:
    """Each sentence becomes a tone of its own (1 s unless `seconds` says), after `delay` (0.1 s unless it says);
    a sentence in `fails` raises."""

    def __init__(self, seconds=None, delay=None, fails=()) -> None:
        self.seconds = seconds or {}
        self.delay = delay or {}
        self.fails = set(fails)
        self.ids: dict[str, int] = {}
        self.calls: list[tuple[float, str]] = []

    async def __call__(self, text: str) -> bytes:
        self.calls.append((asyncio.get_running_loop().time(), text))
        value = self.ids.setdefault(text, 100 + len(self.ids))
        await asyncio.sleep(self.delay.get(text, 0.1))
        if text in self.fails:
            raise RuntimeError("the voice is down")
        return tone(value, self.seconds.get(text, 1.0))


def words(text: str) -> Transcript:
    return Transcript(text, "en", 1.0, no_speech=0.02, avg_logprob=-0.2)


def hiss(text: str = "Thank you.") -> Transcript:
    """What Whisper writes for line noise: its own scores give it away."""
    return Transcript(text, "en", 1.0, no_speech=0.9, avg_logprob=-1.4)


@dataclasses.dataclass
class Call:
    outcome: object
    media: FakeLine
    chat: FakeChat
    ears: FakeEars
    voice: FakeVoice

    def runs(self) -> list[tuple[str, float, float]]:
        """(sound, first frame time, last frame time) for each stretch of one sound; silence between two stretches of
        the same sound (a pause) doesn't split it."""
        names = {v: k for k, v in CLIP_TONES.items()} | {v: t for t, v in self.voice.ids.items()}
        out: list[list] = []
        for at, frame in self.media.frames:
            samples = np.frombuffer(frame, dtype="<i2")
            for value in samples[np.r_[True, samples[1:] != samples[:-1]]]:
                if value == 0:
                    continue
                name = names[int(value)]
                if out and out[-1][0] == name:
                    out[-1][2] = at
                else:
                    out.append([name, at, at])
        return [tuple(r) for r in out]

    def played(self) -> list[str]:
        return [name for name, _, _ in self.runs()]

    def starts(self, name: str) -> float:
        return next(first for n, first, _ in self.runs() if n == name)

    def ends(self, name: str) -> float:
        """When the last frame carrying `name` had gone out."""
        return next(last for n, _, last in reversed(self.runs()) if n == name) + 0.02

    def frames_of(self, value_or_name) -> list[float]:
        value = CLIP_TONES.get(value_or_name) or self.voice.ids[value_or_name]
        return [at for at, frame in self.media.frames if value in np.frombuffer(frame, dtype="<i2")]


def call(script, *, turns=(), heard=(), voice=None, hearing=ScriptedHearing, max_call_s=600, idle_s=30.0,
         checking_s=4.0, max_utterance_s=30.0, begin_s=0.0, finalize_s=0.05, hear_s=0.3) -> Call:
    async def main() -> Call:
        loop = asyncio.get_running_loop()
        media = FakeLine(script)
        chat = FakeChat(turns, begin_s=begin_s, finalize_s=finalize_s)
        ears = FakeEars(heard, seconds=hear_s)
        tts = voice or FakeVoice()
        cfg = PhoneConfig(listen=None, allow=(), pbx_address=None, idle_s=idle_s, checking_s=checking_s,
                          max_utterance_s=max_utterance_s)
        extra = {} if hearing is None else {"hearing": hearing}
        deps = CallDeps(transcribe=ears, begin=chat.begin, run=chat.run, render=tts, clock=loop.time,
                        sleep=asyncio.sleep, settings=SETTINGS, cfg=cfg, **extra)
        line = dataclasses.replace(LINE, max_call_s=max_call_s)
        outcome = await CallSession(media, line, CLIPS, "chat-1", deps).run()
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert not leftover, f"the call left tasks running: {leftover}"
        assert media.hung_up_at is not None and media.late_sends == 0   # R9.6: the pacer stopped, then the hang-up
        return Call(outcome, media, chat, ears, tts)

    return run_virtual(main())


# --- the conversation ---

def test_greeting_is_the_first_audio():
    c = call([(1.0, HungUp("caller"))])
    at, first = c.media.frames[0]
    assert at == 0.0 and first == tone(1, 0.02)
    assert c.played() == ["greeting"]
    assert c.outcome.end_reason == "caller" and c.outcome.turns == 0 and c.outcome.stats == []


def test_question_gets_an_answer():
    c = call([*speech(1.0, 2.0), *speech(5.0, 6.0), (9.0, HungUp("caller"))],
             heard=[words(" What are your hours?"), words("And on Sunday?")],
             turns=[[*says("We open at nine."), FINISH], [*says("We are closed on Sunday."), FINISH]])

    assert c.played() == ["greeting", "We open at nine.", "We are closed on Sunday."]
    first = c.chat.begins[0]
    assert first == {"session_id": "chat-1", "user_id": "line-user-1", "model_id": "ollama:test-model",
                     "text": "What are your hours?", "attachments": [], "new_session_pins": None,
                     "scope_project_id": "project-1", "heard": None}
    # the first reply had played out before the second question: nothing to cut (R9.2)
    assert c.chat.begins[1]["text"] == "And on Sunday?" and c.chat.begins[1]["heard"] is None

    req = c.chat.requests[0]
    assert (req.user_id, req.session_id, req.model_id, req.text) == (
        "line-user-1", "chat-1", "ollama:test-model", "What are your hours?")
    assert req.attachments == () and req.settings.think == "off" and req.doc_id is None and req.timezone is None
    assert (req.project_id, req.spoken, req.channel, req.profile_id) == ("project-1", True, "phone", "profile-1")

    # the clip goes to the recognizer as a 16 kHz mono WAV, under a key of the call's own
    assert c.ears.calls[0]["key"] == "phone:chat-1"
    samples, rate = sf.read(io.BytesIO(c.ears.calls[0]["wav"]), dtype="float32")
    assert rate == 16000 and samples.ndim == 1 and samples.size == 16000

    stat = c.outcome.stats[0]
    assert stat["stopped_at"] == pytest.approx(1.3)            # the VAD's 0.7 s of silence before it said "ended"
    assert stat["heard_at"] == pytest.approx(2.3)
    assert stat["first_text_at"] == pytest.approx(2.3) and stat["first_sentence_at"] == pytest.approx(2.3)
    assert stat["first_audio_at"] == pytest.approx(2.41, abs=0.011)   # the voice took 0.1 s, then the next frame
    assert stat["first_audio_at"] == pytest.approx(c.starts("We open at nine."))
    assert stat["error"] is None
    assert c.outcome.end_reason == "caller" and c.outcome.turns == 2 and len(c.outcome.stats) == 2


def test_words_before_a_tool_play_at_once():
    c = call([*speech(1.0, 2.0), (10.0, HungUp("caller"))], heard=[words("What are your hours?")],
             turns=[[*says("Let me check."), TOOL, 3.0, TOOL_DONE, *says("We open at nine."), FINISH]])
    # "Let me check." ends its step: it is spoken while the tool runs (the tool's result comes at 5.3 s)
    assert c.starts("Let me check.") < 2.5
    assert c.played() == ["greeting", "Let me check.", "We open at nine."]    # no filler: something was said


def test_filler_when_a_tool_starts_silently():
    c = call([*speech(1.0, 2.0), *speech(8.0, 9.0), (15.0, HungUp("caller"))],
             heard=[words("What are your hours?"), words("And Sunday?")],
             turns=[[TOOL, 2.0, TOOL_DONE, *says("We open at nine."), FINISH],
                    [TOOL, 2.0, TOOL_DONE, *says("We close on Sunday."), FINISH]])
    # one filler per turn, taking turns between the two
    assert c.played() == ["greeting", "filler0", "We open at nine.", "filler1", "We close on Sunday."]
    assert c.starts("filler0") == pytest.approx(2.31, abs=0.011)
    # a turn's first sound is its first audio of any kind, the filler here (the bench's first_sound_s)
    assert c.outcome.stats[0]["first_audio_at"] == pytest.approx(c.starts("filler0"))


def test_checking_plays_once_when_a_tool_runs_long():
    c = call([*speech(1.0, 2.0), (16.0, HungUp("caller"))], heard=[words("Is the book in?")],
             turns=[[TOOL, 5.0, TOOL_DONE, TOOL, 5.0, TOOL_DONE, *says("Found it."), FINISH]])
    assert c.played() == ["greeting", "filler0", "checking", "Found it."]
    assert c.starts("checking") == pytest.approx(2.3 + 4.0, abs=0.021)
    assert len(c.frames_of("checking")) == 20                  # once: one 0.4 s clip, not two


@pytest.mark.parametrize("what", [hiss(), words("opens at nine every weekday")], ids=["noise", "echo"])
def test_noise_or_echo_resumes_the_same_reply(what):
    reply = "The library opens at nine every weekday morning."
    c = call([*speech(1.0, 2.0), *speech(3.4, 4.0), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), what], voice=FakeVoice(seconds={reply: 3.0}),
             turns=[[*says(reply), FINISH]])
    assert len(c.chat.begins) == 1                       # no new turn
    sent = c.frames_of(reply)
    assert len(sent) == 150                              # every frame of the 3 s sentence went out
    assert not [at for at in sent if 3.41 < at < 4.29]   # paused from the speech start to the verdict
    assert sent[-1] == pytest.approx(6.3, abs=0.03)      # and carried on from where it paused


def test_the_lines_words_said_after_it_finished_are_the_callers():
    """Talk's echo window: only a clip that began while the line was speaking can be the line's own words."""
    c = call([*speech(1.0, 2.0), *speech(5.0, 6.0), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("nine every weekday")],
             turns=[[*says("We open at nine every weekday."), FINISH], [*says("Yes, nine."), FINISH]])
    assert len(c.chat.begins) == 2 and c.chat.begins[1]["text"] == "nine every weekday"


def test_a_clip_misheard_during_a_reply_lets_it_go_on():
    """The recognizer fails while a reply is paused: the reply resumes (as Talk does); `sorry` is for a quiet line."""
    reply = "The library opens at nine every weekday morning."
    c = call([*speech(1.0, 2.0), *speech(3.4, 4.0), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), RuntimeError("the recognizer is down")],
             voice=FakeVoice(seconds={reply: 3.0}), turns=[[*says(reply), FINISH]])
    assert c.played() == ["greeting", reply] and len(c.frames_of(reply)) == 150
    assert [s["error"] for s in c.outcome.stats] == [None, "hearing"]


def test_the_voice_works_at_most_two_sentences_ahead():
    """Talk's AHEAD: five 1 s sentences, all written at once. The fourth is voiced only once the first is more than
    half heard (2.9 s), the fifth once the second is (3.9 s)."""
    sentences = ["One.", "Two.", "Three.", "Four.", "Five."]
    c = call([*speech(1.0, 2.0), (10.0, HungUp("caller"))], heard=[words("Count to five.")],
             turns=[[*says(*(s + " " for s in sentences)), FINISH]])
    started = [at for at, _ in c.voice.calls]
    assert started[:3] == pytest.approx([2.3, 2.4, 2.5], abs=0.001)
    assert started[3] == pytest.approx(2.92, abs=0.021) and started[4] == pytest.approx(3.92, abs=0.021)
    assert c.played() == ["greeting", *sentences]


def test_a_reply_starting_while_the_caller_talks_waits_for_them():
    """R7.2: new_reply() clears the pause, so a turn that starts while a speech run is open pauses again. The cut
    turn takes 2 s to close; the caller starts again at 5.5 s, and the new reply waits for that clip's verdict."""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.6), *speech(5.5, 7.0), (12.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("Wait."), hiss()], finalize_s=2.0,
             turns=[[*says("One is first."), 10.0, FINISH], [*says("Noted."), FINISH]])
    assert c.chat.begun_at[1] == pytest.approx(5.9)            # the new turn began while the caller was talking
    assert c.starts("Noted.") == pytest.approx(7.31, abs=0.011)  # and its reply waited for the verdict (noise)


@pytest.mark.parametrize("cut_at, heard", [(3.8, "One is first."), (4.0, "One is first. Two is second.")])
def test_interrupt_keeps_only_what_was_heard(cut_at, heard):
    """Review focus 5. Three 1 s sentences from 2.4 s; the caller cuts in at 3.8 s (sentence two 40% played) or
    4.0 s (60%). Talk's rule: a sentence more than half played counts as heard."""
    c = call([*speech(1.0, 2.0), *speech(cut_at, 4.6), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("What about Sunday?")],
             turns=[[*says("One is first. ", "Two is second. ", "Three is third."), 10.0, FINISH],
                    [*says("Sunday is closed."), FINISH]])
    assert c.chat.begins[1]["heard"] == Heard("a1", len(heard))
    assert c.chat.begins[1]["text"] == "What about Sunday?"
    # the cut turn was still writing: its generator was closed and saved before the next claim (R9.2)
    assert c.chat.turns[0]["finalized_at"] <= c.chat.begun_at[1]
    assert "Three is third." not in c.played() and c.played()[-1] == "Sunday is closed."


def test_a_cut_during_a_filler_keeps_nothing_of_the_reply():
    """R7.1: a turn starts a new reply before its filler, so cutting in on the filler keeps 0 characters of this
    reply, not the offset frozen when the previous one was cut."""
    c = call([*speech(1.0, 2.0), *speech(4.0, 4.6), *speech(5.0, 5.6), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("And Sunday?"), words("Never mind.")],
             turns=[[*says("One is first. ", "Two is second. ", "Three is third."), 10.0, FINISH],
                    [TOOL, 5.0, TOOL_DONE, *says("Sunday is closed."), FINISH], [*says("All right."), FINISH]])
    assert c.chat.begins[1]["heard"] == Heard("a1", len("One is first. Two is second."))
    assert c.starts("filler0") == pytest.approx(4.95, abs=0.021)
    assert c.chat.begins[2]["heard"] == Heard("a2", 0)


def test_heard_counts_the_paragraph_between_steps():
    """run_turn saves a later step's text after "\\n\\n" (state.content), and `heard` cuts that saved text."""
    c = call([*speech(1.0, 2.0), *speech(4.6, 5.2), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("And Sunday?")],
             turns=[[*says("Let me check."), TOOL, 0.5, TOOL_DONE, *says("We open at nine. ", "On Sunday we close."),
                     10.0, FINISH], [*says("Yes."), FINISH]])
    assert c.chat.begins[1]["heard"] == Heard("a1", len("Let me check.\n\nWe open at nine."))


def test_clips_said_while_the_reply_stops_are_joined():
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.6), *speech(4.2, 4.8), (12.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("Wait."), words("Make that Sunday.")], finalize_s=2.0,
             turns=[[*says("One is first."), 10.0, FINISH], [*says("Noted."), FINISH]])
    assert len(c.chat.begins) == 2
    assert c.chat.begins[1]["text"] == "Wait. Make that Sunday."


def test_caller_hangup_closes_the_turn():
    """Review focus 2: the generator is closed (its _finalize ran before run() returned), and nothing more is asked
    of the model, the voice or the recognizer."""
    c = call([*speech(1.0, 2.0), (3.0, HungUp("caller"))], heard=[words("When do you open?")],
             turns=[[*says("We open at nine. "), 5.0, *says("On Sunday we close."), FINISH]])
    assert c.outcome.end_reason == "caller"
    assert c.chat.turns[0]["finalized_at"] is not None
    assert [text for _, text in c.voice.calls] == ["We open at nine."] and all(at < 3.0 for at, _ in c.voice.calls)
    assert len(c.chat.requests) == 1 and len(c.ears.calls) == 1
    assert all(at <= 3.0 for at, _ in c.media.frames)     # a frame due at the very instant may still go


def test_a_claim_cut_while_it_is_made_is_given_back():
    """begin_turn is one transaction: cut while it runs, the claim lands all the same. run_turn's opening (no model
    call) is run and closed so its own finally gives the claim back, else the transcript keeps a reply "streaming"."""
    c = call([*speech(1.0, 2.0), (2.5, HungUp("caller"))], heard=[words("When do you open?")], begin_s=0.5,
             turns=[[*says("We open at nine."), FINISH]])
    assert c.outcome.end_reason == "caller" and c.outcome.turns == 1
    assert c.chat.turns[0]["pulled"] == 1 and c.chat.turns[0]["finalized_at"] is not None   # "start", then closed
    assert c.voice.calls == []


def test_idle_then_goodbye():
    """Review focus 1: a caller who says nothing still gets a frame every 20 ms; "still there?" after idle_s, and
    after another idle_s goodbye, played out before the hang-up."""
    c = call([])
    assert c.played() == ["greeting", "still", "goodbye"]
    assert c.starts("still") - c.ends("greeting") == pytest.approx(30.0, abs=0.03)
    assert c.starts("goodbye") - c.ends("still") == pytest.approx(30.0, abs=0.03)
    assert c.outcome.end_reason == "idle"
    times = [at for at, _ in c.media.frames]
    assert max(b - a for a, b in zip(times, times[1:])) == pytest.approx(0.02)   # never a gap: silence counts
    # R9.6: the whole goodbye went out, then the frames stopped, then the hang-up
    assert len(c.frames_of("goodbye")) == 50
    assert times[-1] == c.frames_of("goodbye")[-1] and times[-1] <= c.media.hung_up_at


def test_noise_does_not_restart_the_idle_clock():
    """The idle clock counts from when the line last finished speaking; a sound that turns out to be noise only holds
    it while it is being heard (spec 5.3: "no accepted speech for PHONE_IDLE_S")."""
    c = call([*speech(20.0, 21.0), (35.0, HungUp("caller"))], heard=[hiss()])
    assert c.played() == ["greeting", "still"]
    assert c.starts("still") - c.ends("greeting") == pytest.approx(30.0, abs=0.03)


def test_a_hang_up_in_progress_is_never_cancelled():
    """R9.6: a hang-up cut short can leave the connection open, so cancelling the call doesn't cancel it."""
    class SlowHangUp(FakeLine):
        done_at = None

        async def hang_up(self) -> None:
            await super().hang_up()
            await asyncio.sleep(0.5)
            self.done_at = asyncio.get_running_loop().time()

    async def main() -> SlowHangUp:
        loop = asyncio.get_running_loop()
        media = SlowHangUp([(1.0, HungUp("caller"))])
        chat = FakeChat([])
        deps = CallDeps(transcribe=FakeEars([]), begin=chat.begin, run=chat.run, render=FakeVoice(), clock=loop.time,
                        sleep=asyncio.sleep, settings=SETTINGS,
                        cfg=PhoneConfig(listen=None, allow=(), pbx_address=None), hearing=ScriptedHearing)
        session = asyncio.create_task(CallSession(media, LINE, CLIPS, "chat-1", deps).run())
        await asyncio.sleep(1.2)
        assert media.hung_up_at == 1.0 and media.done_at is None    # hanging up, until 1.55 s
        session.cancel()
        with pytest.raises(asyncio.CancelledError):
            await session
        await asyncio.sleep(1.0)
        return media

    assert run_virtual(main()).done_at == pytest.approx(1.55)


def test_a_sound_that_never_stops_cannot_hold_the_reply():
    """R5.8, the review's probe: hold music or a TV from 4.0 s that never leaves 704 ms of quiet. Its scores sit at 0.4,
    enough to keep a run open (vad_stay) and never enough to open one (vad_start). Uncapped, that run never ends: the
    reply stays paused and the idle clock never starts. Capped at 5 s, the run ends, the clip is heard as noise, the
    reply carries on, and with the line quiet "still there?" and goodbye follow."""
    reply = "The library opens at nine every weekday morning."
    script = [*scores(1.0, (0.9, 1.024), (0.0, 0.704)), *scores(4.0, (0.9, 0.32), (0.4, 40.0)),
              (60.0, HungUp("caller"))]
    c = call(script, hearing=ScoredHearing, max_utterance_s=5, idle_s=10, heard=[words("When do you open?"), hiss()],
             voice=FakeVoice(seconds={reply: 3.0}), turns=[[*says(reply), FINISH]])
    assert c.played() == ["greeting", reply, "still", "goodbye"] and c.outcome.end_reason == "idle"
    sent = c.frames_of(reply)
    assert len(sent) == 150                                    # all of it, in the end
    held = [b for a, b in zip(sent, sent[1:]) if b - a > 0.03]
    assert held == [pytest.approx(9.3, abs=0.03)]              # paused at 4.22 s, resumed on the noise verdict
    samples, _ = sf.read(io.BytesIO(c.ears.calls[1]["wav"]), dtype="float32")
    assert samples.size == 157 * 512                           # the capped clip: 5.02 s, not the whole call
    assert c.starts("still") == pytest.approx(c.ends(reply) + 10, abs=0.03)


def test_a_monologue_arrives_in_clips_and_is_answered_once():
    """R5.8: 12 s of talk without a pause, capped at 5 s, comes as three clips; they are joined into one turn, which
    starts only when the caller stops."""
    c = call([*scores(1.0, (0.9, 12.0), (0.0, 0.704)), (20.0, HungUp("caller"))], hearing=ScoredHearing,
             max_utterance_s=5, heard=[words("Part one."), words("Part two."), words("Part three.")],
             turns=[[*says("Noted."), FINISH]])
    assert len(c.ears.calls) == 3
    assert [b["text"] for b in c.chat.begins] == ["Part one. Part two. Part three."]
    assert c.chat.begun_at[0] == pytest.approx(13.97, abs=0.01)
    assert c.played() == ["greeting", "Noted."]


def test_hearing_cuts_talk_that_never_pauses_at_the_cap():
    """The default hearing, on real audio: question1.wav six times over is one unbroken run (17 s). With a 5 s cap it
    comes out as clips of at most the pre-roll and 5 s each."""
    frames = phone_frames(np.concatenate([load("question1.wav")] * 6))

    def clips(max_run_s):
        hearing = Hearing(SETTINGS, max_run_s)
        return [e.clip for f in frames for e in hearing.push(f) if isinstance(e, SpeechEnded)]

    assert clips(None) == []
    capped = clips(5.0)
    assert len(capped) == 3 and all(clip.size <= (10 + 157) * 512 for clip in capped)


def test_idle_waits_for_a_slow_voice():
    """R9.4: the queue running dry mid-reply (the voice behind real time) is not the line finishing."""
    c = call([*speech(1.0, 2.0), (50.0, HungUp("caller"))], heard=[words("When do you open?")],
             voice=FakeVoice(delay={"Two is second.": 40.0}),
             turns=[[*says("One is first. ", "Two is second."), FINISH]])
    assert c.played() == ["greeting", "One is first.", "Two is second."]


def test_call_limit_says_goodbye():
    """At max_call_s the reply is cut and goodbye plays; the caller can't interrupt it (R9.5)."""
    reply = "We open at nine."
    c = call([*speech(50.0, 51.0), *speech(60.2, 60.8), (90.0, HungUp("caller"))], max_call_s=60, idle_s=120,
             heard=[words("When do you open?")], voice=FakeVoice(seconds={reply: 10.0}),
             turns=[[*says(reply + " "), 30.0, FINISH]])
    assert c.outcome.end_reason == "limit"
    assert c.played() == ["greeting", reply, "goodbye"]
    assert c.frames_of(reply)[-1] <= 60.0                  # cut at the limit, 6 s before its end
    goodbye = c.frames_of("goodbye")
    assert goodbye[0] == pytest.approx(60.0, abs=0.021)
    assert len(goodbye) == 50 and goodbye[-1] - goodbye[0] == pytest.approx(0.98)   # no pause for the caller
    assert len(c.ears.calls) == 1                                                  # nor anything heard
    assert c.chat.turns[0]["finalized_at"] is not None
    assert c.media.frames[-1][0] <= c.media.hung_up_at


def test_turn_error_says_sorry_and_continues():
    c = call([*speech(1.0, 2.0), *speech(4.0, 5.0), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("When do you open?")],
             turns=[[fails("provider_error")], [*says("We open at nine."), FINISH]])
    assert c.played() == ["greeting", "sorry", "We open at nine."]
    assert [s["error"] for s in c.outcome.stats] == ["provider_error", None]
    assert c.outcome.turns == 2 and c.outcome.end_reason == "caller"


def test_a_turn_that_breaks_says_sorry_and_the_call_goes_on():
    """A turn that throws (here a malformed event) is closed like any other, and the caller can ask again."""
    c = call([*speech(1.0, 2.0), *speech(4.0, 5.0), (9.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("When do you open?")],
             turns=[[{"type": "text-delta", "delta": 5}, 10.0, FINISH], [*says("We open at nine."), FINISH]])
    assert c.played() == ["greeting", "sorry", "We open at nine."]
    assert c.outcome.stats[0]["error"] == "internal"
    assert c.chat.turns[0]["finalized_at"] == pytest.approx(2.35)   # closed at once, not after its 10 s


def test_transcription_failure_says_sorry():
    c = call([*speech(1.0, 2.0), *speech(4.0, 5.0), (9.0, HungUp("caller"))],
             heard=[RuntimeError("the recognizer is down"), words("When do you open?")],
             turns=[[*says("We open at nine."), FINISH]])
    assert c.played() == ["greeting", "sorry", "We open at nine."]     # and it kept listening
    assert c.outcome.stats[0]["error"] == "hearing" and c.outcome.stats[0]["heard_at"] == pytest.approx(2.3)
    assert len(c.chat.begins) == 1


def test_voice_failure_skips_the_sentence():
    c = call([*speech(1.0, 2.0), (9.0, HungUp("caller"))], heard=[words("When do you open?")],
             voice=FakeVoice(fails={"Two is second."}),
             turns=[[*says("One is first. ", "Two is second. ", "Three is third."), FINISH]])
    assert c.played() == ["greeting", "One is first.", "Three is third."]
    assert c.outcome.stats[0]["error"] == "voice"


@pytest.mark.parametrize("turn, voice", [([FINISH], None), ([*says("We open at nine."), FINISH],
                                                              FakeVoice(fails={"We open at nine."}))],
                         ids=["empty", "voice-down"])
def test_a_turn_that_says_nothing_plays_sorry(turn, voice):
    c = call([*speech(1.0, 2.0), (6.0, HungUp("caller"))], heard=[words("When do you open?")], voice=voice,
             turns=[turn])
    assert c.played() == ["greeting", "sorry"]


def test_budget_error_closes_the_call():
    c = call([*speech(1.0, 2.0), (20.0, HungUp("caller"))], heard=[words("When do you open?")],
             turns=[[fails("budget_exhausted")]])
    assert c.outcome.end_reason == "budget"
    assert c.played() == ["greeting", "closed"]
    assert c.media.hung_up_at == pytest.approx(c.ends("closed"), abs=0.021)


def test_digits_are_ignored():
    c = call([(1.0, Digit("1")), *speech(2.0, 3.0), (3.5, Digit("#")), (7.0, HungUp("caller"))],
             heard=[words("When do you open?")], turns=[[*says("We open at nine."), FINISH]])
    assert c.played() == ["greeting", "We open at nine."]
    assert len(c.chat.begins) == 1 and len(c.ears.calls) == 1


def test_an_error_from_the_line_ends_as_error():
    assert call([(1.0, HungUp("error"))]).outcome.end_reason == "error"


def test_a_misfire_changes_nothing():
    c = call([(1.0, START), (1.5, MISFIRE), *speech(2.0, 3.0), (7.0, HungUp("caller"))],
             heard=[words("When do you open?")], turns=[[*says("We open at nine."), FINISH]])
    assert c.played() == ["greeting", "We open at nine."] and len(c.ears.calls) == 1


def test_logs_carry_no_words(caplog):
    caplog.set_level(logging.DEBUG)
    call([*speech(1.0, 2.0), *speech(5.0, 6.0), *speech(8.0, 9.0), (14.0, HungUp("caller"))],
         heard=[words("Where is the secret room?"), RuntimeError("down"), words("Tell me again.")],
         voice=FakeVoice(fails={"Behind the shelf."}),
         turns=[[*says("Behind the shelf. ", "Next to the atlas."), FINISH], [fails("provider_error")]])
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "phone call" in text
    for secret in ("secret room", "Behind the shelf", "atlas", "Tell me again"):
        assert secret not in text


# --- babble mode (R16.1): constant background sound, such as a TV beside the phone ---

WEEKDAYS = "The library opens at nine every weekday morning."


def held(sent: list[float], start: float, end: float) -> bool:
    """No frame of the sound went out between `start` and `end` (a frame due at either edge may still go)."""
    return not [at for at in sent if start + 0.01 < at < end - 0.01]


def flowed(sent: list[float], start: float, end: float) -> bool:
    """The sound went out frame after frame, every 20 ms, from `start` to `end`: never held."""
    inside = [at for at in sent if start <= at <= end]
    return bool(inside) and inside[0] - start < 0.03 and end - inside[-1] < 0.03 \
        and all(b - a < 0.03 for a, b in zip(inside, inside[1:]))


def test_babble_mode_starts_at_the_second_noise_verdict():
    """Each stretch of the TV's sound pauses the reply until the recognizer calls it noise. After the second noise
    verdict in a row the call is in babble mode: the third stretch doesn't pause the reply, which goes on frame after
    frame through it and past its verdict."""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.6), *speech(4.5, 5.0), *speech(6.0, 7.0), (14.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), hiss()], voice=FakeVoice(seconds={WEEKDAYS: 8.0}),
             turns=[[*says(WEEKDAYS), FINISH]])
    sent = c.frames_of(WEEKDAYS)
    assert len(sent) == 400                                          # all 8 s of it
    assert held(sent, 3.0, 3.9) and held(sent, 4.5, 5.3)             # the first two stretches pause it
    assert flowed(sent, 6.0, 7.3)                                    # the third doesn't
    assert len([b for a, b in zip(sent, sent[1:]) if b - a > 0.03]) == 2
    assert len(c.chat.begins) == 1


def test_babble_mode_lets_the_reply_go_on_while_a_stretch_is_already_open():
    """Constant babble starts again before the recognizer has judged the last stretch, so a run is open when the second
    noise verdict lands (here from 4.7 s to 9.0 s). Babble mode lifts the pause that run put on there and then: the
    reply goes on from the verdict, not from when that run ends."""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.6), (4.0, START), (4.6, END), (4.7, START), (9.0, END),
              (14.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), hiss()], voice=FakeVoice(seconds={WEEKDAYS: 8.0}),
             turns=[[*says(WEEKDAYS), FINISH]])
    sent = c.frames_of(WEEKDAYS)
    assert held(sent, 4.0, 4.9)                                      # the second stretch pauses it, to its verdict
    assert flowed(sent, 4.95, 9.3)                                   # then it plays through the open one
    assert len(sent) == 400


def test_a_speech_verdict_in_babble_mode_answers_and_babble_mode_stays_on(caplog):
    """In babble mode the caller's question doesn't pause the reply, so the reply plays over them until the verdict.
    That verdict cuts it there: the model keeps what was really played by then (two sentences; paused at the speech
    start, as normal mode does, it would have been one). The next turn starts with the question. The room is still
    loud, so the call stays in babble mode (R16.2): the next stretch, with no quiet before it, doesn't pause the new
    reply."""
    caplog.set_level(logging.DEBUG, logger="server.phone.call")
    # every gap between stretches is under QUIET_S: 1.5 s before the question, 1.0 s after it
    c = call([*speech(1.0, 2.0), *speech(2.5, 3.0), *speech(4.0, 4.5), *speech(6.0, 7.0), *speech(8.0, 8.6),
              (13.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), words("What about Sunday?"), hiss()],
             voice=FakeVoice(seconds={"Sunday is closed.": 3.0}),
             turns=[[3.0, *says("One is first. ", "Two is second. ", "Three is third. ", "Four is fourth."), 10.0,
                     FINISH], [*says("Sunday is closed."), FINISH]])
    # the reply began at 5.4 s, after the two noise verdicts; at the 7.3 s verdict "Two is second." was 90% played
    assert c.chat.begins[1]["heard"] == Heard("a1", len("One is first. Two is second."))
    assert c.chat.begins[1]["text"] == "What about Sunday?"
    assert c.played() == ["greeting", "One is first.", "Two is second.", "Sunday is closed."]
    assert flowed(c.frames_of("Sunday is closed."), 8.0, 8.9)       # still babble mode: 1 s since the last stretch
    assert c.outcome.stats[1]["babble"] is True                     # the question was judged in babble mode
    assert "babble" not in c.outcome.stats[0]
    logged = [r.getMessage() for r in caplog.records if "babble" in r.getMessage()]
    assert logged == ["phone call chat-1: babble mode on, after 2 noise clips in a row"]   # on, and never off
    assert not [r for r in caplog.records if "Sunday" in r.getMessage()]


def test_in_constant_babble_a_question_is_answered_at_its_verdict():
    """R16.2, Task 15's Concern 1. Babble from 3.0 s to 30.0 s, as 1 s stretches 0.2 s apart; the recognizer takes
    0.3 s, so every verdict lands with the next stretch already open. The caller's question is the third stretch,
    judged speech at 6.7 s. That stretch's verdict cuts the reply and starts the next turn at once (after the cut turn's
    0.05 s close), not when the sound stops (29.55 s, built to R16.1). The new reply then plays through the babble."""
    runs = [(3.0 + 1.2 * k, 4.0 + 1.2 * k) for k in range(22)]
    c = call([*speech(1.0, 2.0), *(e for a, b in runs for e in speech(a, b)), (40.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), words("What about Sunday?"), *[hiss()] * 19],
             voice=FakeVoice(seconds={WEEKDAYS: 30.0, "Sunday is closed.": 3.0}),
             turns=[[*says(WEEKDAYS), 60.0, FINISH], [*says("Sunday is closed."), FINISH]])
    assert c.chat.begins[1]["text"] == "What about Sunday?"
    assert c.chat.begun_at[1] == pytest.approx(6.75, abs=0.02)
    assert c.frames_of(WEEKDAYS)[-1] <= 6.72                         # the old reply stopped at the verdict
    sunday = c.frames_of("Sunday is closed.")
    assert len(sunday) == 150 and flowed(sunday, sunday[0], sunday[-1])   # 3 s, never held


def test_entering_babble_mode_lets_go_of_the_open_stretch():
    """R16.2: a stretch that opened in normal mode holds; the noise verdict that brings babble mode turns it into one
    that doesn't. Here clip B is judged noise (the second in a row) at 4.8 s, while clip C waits to be transcribed and
    stretch X is open (4.75 s to 9.0 s). C, the caller's question, is judged speech at 5.1 s and answered then: it
    doesn't wait for X to end and be judged (9.3 s)."""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.5), *speech(4.0, 4.5), *speech(4.55, 4.7), (4.75, START), (9.0, END),
              (14.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), words("What about Sunday?"), hiss()],
             voice=FakeVoice(seconds={WEEKDAYS: 8.0}),
             turns=[[*says(WEEKDAYS), FINISH], [*says("Sunday is closed."), FINISH]])
    assert c.chat.begun_at[1] == pytest.approx(5.1, abs=0.011)
    assert c.chat.begins[1]["text"] == "What about Sunday?"
    assert flowed(c.frames_of("Sunday is closed."), 5.25, 6.1)       # and its reply plays while X is open


@pytest.mark.parametrize("gap, babble_after", [(2.1, False), (1.9, True)], ids=["quiet", "not-quiet-enough"])
def test_only_quiet_ends_babble_mode(gap, babble_after, caplog):
    """R16.2: babble mode ends when the line had no stretch open for QUIET_S (2.0 s), from the last stretch's end to
    the next speech start. Then that stretch holds, and pauses the reply, as in normal mode. Just under QUIET_S, the
    call stays in babble mode and the reply flows on. The log line carries the call's id and nothing else."""
    caplog.set_level(logging.DEBUG, logger="server.phone.call")
    start = 4.5 + gap                                                # the last stretch ended at 4.5 s
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.5), *speech(4.0, 4.5), *speech(start, start + 0.5),
              (18.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), hiss()], voice=FakeVoice(seconds={WEEKDAYS: 12.0}),
             turns=[[*says(WEEKDAYS), FINISH]])
    sent = c.frames_of(WEEKDAYS)
    assert (flowed if babble_after else held)(sent, start, start + 0.8)
    off = [r.getMessage() for r in caplog.records if "babble mode off" in r.getMessage()]
    assert off == ([] if babble_after else ["phone call chat-1: babble mode off: the line went quiet"])


@pytest.mark.parametrize("what", ["echo", "misheard", "misfire"])
def test_echo_a_misheard_clip_and_a_misfire_leave_the_noise_count_alone(what):
    """One noise verdict, then an echo, a clip the recognizer failed on, or a misfire, then a second noise verdict.
    The stretch after the middle one still pauses the reply (it didn't count as noise), and the stretch after the
    second noise verdict doesn't (it didn't reset the count either)."""
    # a misfire comes alone: the segmenter calls it when a run ends before it was ever called speech
    middle = [(4.5, MISFIRE)] if what == "misfire" else speech(4.0, 4.5)
    heard_middle = {"echo": [words("opens at nine every weekday")],
                    "misheard": [RuntimeError("the recognizer is down")], "misfire": []}[what]
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.5), *middle, *speech(5.0, 5.5), *speech(6.5, 7.0),
              (18.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), *heard_middle, hiss(), hiss()],
             voice=FakeVoice(seconds={WEEKDAYS: 12.0}), turns=[[*says(WEEKDAYS), FINISH]])
    sent = c.frames_of(WEEKDAYS)
    assert held(sent, 5.0, 5.8)                                      # the count was 1: normal mode
    assert flowed(sent, 6.5, 7.3)                                    # the count was 2: babble mode
    assert len(sent) == 600 and len(c.chat.begins) == 1


def test_a_reply_starting_while_a_stretch_is_open_in_babble_mode_plays_at_once():
    """R7.2 in babble mode. The caller cuts in ("Wait."), and the cut turn takes 3 s to close. Meanwhile two stretches
    of the TV are judged noise, and a third opens at 6.0 s. The new turn begins at 6.9 s, while that run is open, and
    its reply plays at once: in normal mode it would wait for that run's verdict at 8.3 s."""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.6), *speech(4.0, 4.5), *speech(5.0, 5.5), *speech(6.0, 8.0),
              (12.0, HungUp("caller"))],
             heard=[words("When do you open?"), words("Wait."), hiss(), hiss(), hiss()], finalize_s=3.0,
             turns=[[*says("One is first."), 10.0, FINISH], [*says("Noted."), FINISH]])
    assert c.chat.begun_at[1] == pytest.approx(6.9)                  # the new turn began while the run was open
    assert c.starts("Noted.") == pytest.approx(7.01, abs=0.011)      # and its reply didn't wait for it


def test_one_noise_verdict_is_not_babble_mode():
    """Normal mode is unchanged: after one noise verdict, the next stretch of sound still pauses the reply until its
    own verdict."""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.6), *speech(4.5, 5.0), (12.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss()], voice=FakeVoice(seconds={WEEKDAYS: 6.0}),
             turns=[[*says(WEEKDAYS), FINISH]])
    sent = c.frames_of(WEEKDAYS)
    assert held(sent, 3.0, 3.9) and held(sent, 4.5, 5.3) and len(sent) == 300


def test_two_coughs_far_apart_in_a_quiet_room_are_not_babble():
    """R16.3, the review's probe. A quiet room: a cough at 3.0 s and another at 8.0 s, 4.7 s apart, each judged noise.
    The quiet between them breaks the row, so the second cough counts as the first again, and the caller's real
    interruption at 9.5 s pauses the reply at once, as in normal mode. (Without the break the coughs made babble mode,
    and the reply played over the caller until their clip was judged at 10.8 s.)"""
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.3), *speech(8.0, 8.3), *speech(9.5, 10.5), (16.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss("Ahem."), hiss("Ahem."), words("Sorry, and on Sunday?")],
             voice=FakeVoice(seconds={WEEKDAYS: 12.0}), turns=[[*says(WEEKDAYS), FINISH], [*says("Closed."), FINISH]])
    assert max(c.frames_of(WEEKDAYS)) <= 9.52                        # nothing of the reply over the cut-in
    assert c.chat.begun_at[1] == pytest.approx(10.8)                  # answered at its verdict
    assert not [s for s in c.outcome.stats if "babble" in s]


@pytest.mark.parametrize("gap, babble_after", [(2.1, False), (1.9, True)], ids=["quiet-between", "close-together"])
def test_quiet_between_two_noise_clips_breaks_the_row(gap, babble_after):
    """R16.3: noise, then QUIET_S or more with nothing open, then noise, is one noise verdict and another, not two in
    a row: the next stretch still pauses the reply. With under QUIET_S between them, they are two in a row, and the
    call is in babble mode: the next stretch doesn't pause it."""
    second = 3.5 + gap                                                # the first noise stretch ended at 3.5 s
    third = second + 1.0                                              # 0.5 s after the second ends
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.5), *speech(second, second + 0.5), *speech(third, third + 0.5),
              (18.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), hiss()], voice=FakeVoice(seconds={WEEKDAYS: 12.0}),
             turns=[[*says(WEEKDAYS), FINISH]])
    sent = c.frames_of(WEEKDAYS)
    assert held(sent, second, second + 0.8)                           # the second stretch pauses it either way
    assert (flowed if babble_after else held)(sent, third, third + 0.8)


def test_a_noise_verdict_from_before_the_quiet_does_not_count_after_it(caplog):
    """R16.3 with a slow recognizer (2.5 s a clip, as on a busy CPU-only machine). Clip A ends at 1.5 s; the line is
    quiet until stretch B starts at 3.6 s, which breaks the row. A's verdict (noise) only lands at 4.0 s, after the
    break: it belongs to the row the quiet ended, so it doesn't count. B's noise is then the first in the new row, and
    the caller's question E, judged at 9.2 s, is judged in normal mode."""
    caplog.set_level(logging.DEBUG, logger="server.phone.call")
    c = call([*speech(1.0, 1.5), *speech(3.6, 4.2), *speech(4.5, 5.0), (14.0, HungUp("caller"))], hear_s=2.5,
             heard=[hiss(), hiss(), words("When do you open?")], turns=[[*says("We open at nine."), FINISH]])
    assert c.chat.begun_at[0] == pytest.approx(9.2)
    assert "babble" not in c.outcome.stats[0]
    assert not [r for r in caplog.records if "babble mode on" in r.getMessage()]


def test_a_misheard_clip_in_babble_mode_gets_sorry_at_once_on_a_quiet_line():
    """R16.2, pinned: with nothing being said or written, a clip the recognizer failed on gets `sorry` at its verdict
    (3.8 s), even though stretch D is open: in babble mode D doesn't hold, so `sorry` doesn't wait for D to end and be
    judged (8.3 s). The misheard entry is marked as judged in babble mode."""
    c = call([*speech(1.0, 1.5), *speech(2.0, 2.5), *speech(3.0, 3.5), (3.6, START), (8.0, END),
              (12.0, HungUp("caller"))],
             heard=[hiss(), hiss(), RuntimeError("the recognizer is down"), hiss()])
    assert c.starts("sorry") == pytest.approx(3.81, abs=0.011)
    [misheard] = c.outcome.stats
    assert misheard["error"] == "hearing" and misheard["babble"] is True


@pytest.mark.parametrize("what", ["misfire", "echo", "misheard"])
def test_quiet_is_counted_from_the_last_run_however_it_ended(what):
    """R16.2's quiet clock, pinned: it restarts at every run's end, whatever came of the run. Babble mode is on from
    4.8 s; the last noise stretch ended at 4.5 s, and a lone misfire (a run never called speech), an echo or a misheard
    clip ends at 5.5 s. Stretch C starts at 6.6 s: 2.1 s after the noise, but only 1.1 s after that run, so the line
    wasn't quiet for QUIET_S and the reply flows on through C."""
    middle = [(5.5, MISFIRE)] if what == "misfire" else speech(5.0, 5.5)
    heard_middle = {"misfire": [], "echo": [words("opens at nine every weekday")],
                    "misheard": [RuntimeError("the recognizer is down")]}[what]
    c = call([*speech(1.0, 2.0), *speech(3.0, 3.5), *speech(4.0, 4.5), *middle, *speech(6.6, 7.1),
              (18.0, HungUp("caller"))],
             heard=[words("When do you open?"), hiss(), hiss(), *heard_middle, hiss()],
             voice=FakeVoice(seconds={WEEKDAYS: 12.0}), turns=[[*says(WEEKDAYS), FINISH]])
    assert flowed(c.frames_of(WEEKDAYS), 6.6, 7.4)


# --- hearing the caller ---

def phone_frames(audio_16k: np.ndarray) -> list[bytes]:
    """What a call carries: 8 kHz s16le in 320-byte frames."""
    pcm = (np.clip(resample_poly(audio_16k, 1, 2), -1.0, 1.0) * 32767).astype("<i2").tobytes()
    pcm += b"\0" * (-len(pcm) % 320)
    return [pcm[i:i + 320] for i in range(0, len(pcm), 320)]


def load(name: str) -> np.ndarray:
    samples, rate = sf.read(FIXTURES / name, dtype="float32")
    assert rate == 16000
    return samples


def test_the_upsampler_matches_resample_poly_frame_by_frame():
    """Frames resampled one by one would click at every edge; the upsampler carries its filter across them and gives
    resample_poly's samples for the whole stream, 20 samples (1.25 ms) late."""
    frames = phone_frames(load("question1.wav"))
    whole = np.frombuffer(b"".join(frames), dtype="<i2").astype(np.float64) / 32768.0
    up = Upsampler()
    streamed = np.concatenate([up.push(f) for f in frames])
    np.testing.assert_allclose(streamed[20:], resample_poly(whole, 2, 1)[:-20], atol=1e-6)


def test_a_real_question_through_the_vad_gets_answered():
    """R9.1: question1.wav, as a phone line carries it, through the real upsampler, Silero and the segmenter."""
    noise = load("silence_2s.wav")
    frames = phone_frames(np.concatenate([noise[:16000], load("question1.wav"), noise, noise]))
    script = [(0.02 * i, Audio(frame)) for i, frame in enumerate(frames)] + [(10.0, HungUp("caller"))]
    question = "What time does the library open on Saturday?"
    c = call(script, hearing=None, heard=[words(question)], turns=[[*says("We open at ten on Saturday."), FINISH]])

    assert len(c.ears.calls) == 1
    samples, rate = sf.read(io.BytesIO(c.ears.calls[0]["wav"]), dtype="float32")
    assert rate == 16000 and samples.ndim == 1
    assert 2.5 < samples.size / 16000 < 5.0                       # the question, its pre-roll and trailing silence
    assert c.chat.begins[0]["text"] == question
    assert c.played() == ["greeting", "We open at ten on Saturday."]
    assert 1.0 + 2.0 < c.outcome.stats[0]["stopped_at"] < 1.0 + 3.0   # the question is 2.9 s, from 1.0 s
