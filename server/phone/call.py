"""One phone call, heard and answered: Talk's spoken conversation (src/lib/speech/conversation.js) run on the
server (spec §5.3).

Three tasks share the call:
- the receiver turns the caller's 8 kHz frames into "speech started" and "speech ended, here's the clip" (`Hearing`:
  8 -> 16 kHz, Silero, the segmenter);
- the pacer sends a frame every 20 ms whatever happens: the next audio, or silence;
- the conversation (`_converse`) is the one consumer of everything that happens: the receiver's events, a clip's
  transcript, a turn ending, the pacer running dry, the idle and call-length timers. Only it changes the call's state,
  so no two of those decisions interleave.

A turn (`_run_turn`) is a task of its own. It makes the claim (`begin`), iterates the chat engine's `run_turn`
directly (no HTTP), cuts the reply into sentences, and voices them into the pacer, at most `AHEAD` ahead of what the
caller has heard.

Talk's rules, ported:
- speech started: pause the reply. The turn keeps writing and the voice keeps working behind the pause;
- speech ended: transcribe the clip, then Talk's verdict. Noise or echo resumes the reply where it paused. Speech
  stops it, closes the turn (its `_finalize` saves the reply and gives the claim back), and starts the next turn with
  what the caller actually heard (`Heard`), joined with anything else they said while it stopped.
The phone adds: words written before a tool are spoken at once (`text-end`); a filler when a tool starts with nothing
said yet, and "still checking" once if no text follows for `checking_s`; `sorry` for a failed or empty answer;
"are you still there?" and then goodbye after silence; goodbye at the call's time limit; `closed` when the budget runs
out. An ending can't be interrupted: its clip plays out, the pacer stops, then the call hangs up.

The pacer, the voices and the clips are 8 kHz, so a call's media is too (AudioSocket's rate).

Logs carry ids, durations and end reasons only, never what anyone said."""
from __future__ import annotations

import asyncio
import io
import logging
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import firwin, lfilter

from server.chat.orchestrator import TurnRequest
from server.chat.store import Heard, TurnClaim
from server.llm.types import CallSettings
from server.speech.base import Transcript

from .config import PhoneConfig
from .media import CallMedia, Digit, HungUp
from .pacer import FRAME_S, SILENCE, Pacer
from .segmenter import Event, Misfire, Segmenter, SpeechEnded, SpeechStarted
from .sentences import Piece, SentenceSplitter
from .store import Line
from .vad import SileroStream
from .verdict import verdict

logger = logging.getLogger(__name__)

AHEAD = 2                 # sentences voiced beyond the one playing (Talk's AHEAD)
HEARING_RATE = 16000      # what the VAD and the recognizer take
_UP = HEARING_RATE // 8000
# resample_poly(x, 2, 1)'s own low-pass filter (scipy's default for that ratio), run with its state carried from frame
# to frame. Resampling each 20 ms frame on its own would put a click at every frame edge, fifty a second, and the
# recognizer would hear them.
_TAPS = firwin(2 * 10 * _UP + 1, 1 / _UP, window=("kaiser", 5.0)) * _UP


class Upsampler:
    """A call's 8 kHz s16le frames as one 16 kHz float32 stream in [-1, 1]: the samples `resample_poly(x, 2, 1)`
    gives for the whole call, 20 samples (1.25 ms) later (R9.3)."""

    def __init__(self) -> None:
        self._state = np.zeros(len(_TAPS) - 1)

    def push(self, pcm: bytes) -> np.ndarray:
        samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
        stuffed = np.zeros(samples.size * _UP)
        stuffed[::_UP] = samples
        out, self._state = lfilter(_TAPS, 1.0, stuffed, zi=self._state)
        return out.astype(np.float32)


class Hearing:
    """The caller's side of the line: frames in, the segmenter's events out. One per call: the VAD and the filter keep
    state from frame to frame. `max_run_s` caps one clip (R5.8), so unbroken sound can't hold the call."""

    def __init__(self, settings: dict, max_run_s: float | None = None) -> None:
        self._up = Upsampler()
        self._vad = SileroStream()
        self._segmenter = Segmenter(settings, max_run_s=max_run_s)

    def push(self, pcm: bytes) -> list[Event]:
        events: list[Event] = []
        for probability, window in self._vad.push_windows(self._up.push(pcm)):
            events += self._segmenter.step(probability, window)
        return events


@dataclass
class CallDeps:
    """What a call uses from the rest of the app; tests pass fakes."""
    transcribe: Callable[[bytes, str], Awaitable[Transcript]]   # (16 kHz WAV, key): transcribe_for(key, wav, ...)
    begin: Callable[..., Awaitable[TurnClaim]]                  # store.begin_turn
    run: Callable[[TurnRequest, TurnClaim], AsyncIterator[dict]]   # run_turn(req, claim, router=..., ...)
    render: Callable[[str], Awaitable[bytes]]                   # a sentence in the line's voice, s16le 8 kHz
    clock: Callable[[], float]                                  # monotonic seconds
    sleep: Callable[[float], Awaitable[None]]
    settings: dict                                              # server/speech/window.settings()
    cfg: PhoneConfig
    # (settings, cfg.max_utterance_s). R9.1: tests may script the VAD's events
    hearing: Callable[[dict, float | None], Hearing] = Hearing


@dataclass(frozen=True)
class CallOutcome:
    end_reason: str      # caller | error | idle | limit | budget
    turns: int           # claims made
    stats: list[dict]    # one per turn, and one per clip the recognizer failed on (see _stat)


def _stat(stopped_at: float) -> dict[str, Any]:
    """A turn's timings, in seconds since the call started (None: it never got there), and its error, if any. A
    turn's "first sound" is first_audio_at - stopped_at, the figure Talk's clipTimes calls firstSound."""
    return {"stopped_at": stopped_at, "heard_at": None, "first_text_at": None, "first_sentence_at": None,
            "first_audio_at": None, "error": None}


def _wav(samples: np.ndarray) -> bytes:
    out = io.BytesIO()
    sf.write(out, np.clip(samples, -1.0, 1.0), HEARING_RATE, format="WAV", subtype="PCM_16")
    return out.getvalue()


@dataclass
class _Said:
    """The latest thing the line said, for the echo check: Talk's `lastSpoken`."""
    sentences: list[str]
    finished_at: float | None = None     # when it finished playing, or was cut


class _Turn:
    def __init__(self, text: str, heard: Heard | None, stat: dict[str, Any]) -> None:
        self.text = text
        self.heard = heard
        self.stat = stat
        self.task: asyncio.Task | None = None
        self.message_id: str | None = None   # the claim's assistant message
        self.closed = False                  # cut, or the call is over: nothing more of it plays or is asked for
        self.finished = False                # it ran to its end
        self.played_all = False              # ... and every frame of it went out
        self.said = _Said([])
        self.ends: list[int] = []            # each voiced sentence's end in the reply
        self.pieces = 0                      # sentences cut from the reply so far
        self.filled = False                  # its filler has played
        self.checked = False                 # its "still checking" has played
        self.checking: asyncio.Task | None = None
        self.voice_failed = False
        self.error: str | None = None        # the model's error code, or "begin" / "internal"


# What the conversation hears about, besides the receiver's SpeechStarted and Misfire and the media's HungUp.

@dataclass(frozen=True)
class _Clip:
    samples: np.ndarray
    ended_at: float                      # seconds since the call started
    stat: dict[str, Any]


@dataclass(frozen=True)
class _Transcribed:
    clip: _Clip
    transcript: Transcript | None        # None: the recognizer failed


@dataclass(frozen=True)
class _TurnOver:
    turn: _Turn


@dataclass(frozen=True)
class _Switched:
    """The turn cut by the caller has closed: the next one may begin."""


@dataclass(frozen=True)
class _Drained:
    """The pacer's queue ran dry after audio went out."""


@dataclass(frozen=True)
class _Idle:
    gen: int


@dataclass(frozen=True)
class _Limit:
    pass


@dataclass(frozen=True)
class _LineDown:
    """The pacer's send failed: the caller is gone."""


@dataclass(frozen=True)
class _Deaf:
    """The receiver failed: the caller can't be heard any more."""


class CallSession:
    def __init__(self, media: CallMedia, line: Line, clips: dict[str, list[bytes]], session_id: str,
                 deps: CallDeps) -> None:
        self._media = media
        self._line = line
        self._clips = clips
        self._session_id = session_id
        self._deps = deps
        self._key = f"phone:{session_id}"     # the recognizer's one-at-a-time guard: per call, never per line
        self._pacer = Pacer(self._send, deps.clock, deps.sleep)
        self._inbox: asyncio.Queue = asyncio.Queue()
        self._tasks: set[asyncio.Task] = set()
        self._t0 = 0.0
        self._turn: _Turn | None = None       # the latest turn
        self._turns = 0
        self._stats: list[dict[str, Any]] = []
        self._first_audio: dict[str, Any] | None = None   # the stats entry waiting for its first sound
        self._said: _Said | None = None
        self._hearing = False                 # a speech run is open
        self._waiting: deque[_Clip] = deque()  # clips still to transcribe, in order
        self._transcribing: asyncio.Task | None = None
        self._accepted: list[tuple[str, dict[str, Any]]] = []   # what the caller said, for the next turn
        self._misheard: dict[str, Any] | None = None   # a clip the recognizer failed on (its stats entry)
        self._switching: tuple[str, dict[str, Any], Heard | None] | None = None   # the turn waiting for a cut one
        self._fillers = 0
        self._idle_task: asyncio.Task | None = None
        self._idle_at: float | None = None    # when the idle clock runs out; kept while the caller speaks
        self._idle_gen = 0
        self._idle_stage = 0                  # 0: the next idle asks "still there?"; 1: it says goodbye
        self._ending: str | None = None       # the end reason, once an ending clip is playing
        self._stopped = False                 # the caller is gone, or the call is closing: start nothing

    async def run(self) -> CallOutcome:
        self._t0 = self._deps.clock()
        self._pacer.on_drained(lambda: self._post(_Drained()))
        self._said = _Said([self._line.greeting])
        self._play_clip("greeting")
        pacer = self._spawn(self._pacer.run(), self._pacer_stopped)
        self._spawn(self._receive(), self._receiver_stopped)
        self._spawn(self._limit())
        self._idle_if_quiet()                 # without a greeting clip, the idle clock starts now
        reason = "error"
        try:
            reason = await self._converse()
        except Exception:
            logger.exception("phone call %s: the call failed", self._session_id)
        finally:
            await self._finish(pacer)
        logger.info("phone call line=%s session=%s ended reason=%s turns=%d seconds=%.1f", self._line.id,
                    self._session_id, reason, self._turns, self._deps.clock() - self._t0)
        return CallOutcome(reason, self._turns, self._stats)

    # --- the conversation ---

    async def _converse(self) -> str:
        while True:
            event = await self._inbox.get()
            if isinstance(event, HungUp):
                return self._ending or ("error" if event.reason == "error" else "caller")
            if isinstance(event, _LineDown):
                return self._ending or "caller"
            if self._ending:
                # R9.5: an ending isn't interruptible. It is over when its clip has gone out.
                if isinstance(event, _Drained) and not self._pacer.busy():
                    return self._ending
                continue
            if not self._stopped:
                self._handle(event)

    def _handle(self, event: object) -> None:
        match event:
            case SpeechStarted():
                self._hearing = True
                self._pacer.pause()
                self._suspend_idle()
            case Misfire():
                self._hearing = False
                self._settle()
            case _Clip():
                self._hearing = False
                self._waiting.append(event)
                self._drain()
            case _Transcribed():
                self._transcribed(event)
            case _TurnOver(turn=turn):
                self._turn_over(turn)
            case _Switched():
                self._switched()
            case _Drained():
                self._drained()
            case _Idle(gen=gen):
                self._idle(gen)
            case _Limit():
                self._end("limit", "goodbye", cut=True)
            case _Deaf():
                self._end("error", "goodbye", cut=True)

    def _transcribed(self, event: _Transcribed) -> None:
        self._transcribing = None
        clip = event.clip
        clip.stat["heard_at"] = self._at()
        if event.transcript is None:
            clip.stat["error"] = "hearing"
            self._stats.append(clip.stat)
            self._misheard = clip.stat
        else:
            kind = verdict(event.transcript, self._echoable(clip), self._deps.settings)
            logger.debug("phone call %s: a clip was %s", self._session_id, kind)
            if kind == "speech":
                self._accepted.append((event.transcript.text.strip(), clip.stat))
        self._drain()
        self._settle()

    def _echoable(self, clip: _Clip) -> list[str]:
        """Only a clip that began while the line was speaking can be the line's own words (Talk's rule)."""
        said = self._said
        if said is None:
            return []
        began = clip.ended_at - len(clip.samples) / HEARING_RATE
        return said.sentences if said.finished_at is None or began <= said.finished_at else []

    def _settle(self) -> None:
        """Everything that was waiting on the caller has arrived: answer what they said, or carry on."""
        if self._hearing or self._transcribing or self._waiting or self._switching or self._stopped:
            return
        if self._accepted:
            text = " ".join(said for said, _ in self._accepted)
            stat = self._accepted[0][1]
            self._accepted.clear()
            self._misheard = None
            self._answer(text, stat)
            return
        self._pacer.resume()
        misheard, self._misheard = self._misheard, None
        if misheard is not None and not self._busy():
            # The caller said something nobody could make out, and the line is quiet: ask again (spec §5.11). While
            # a reply is still going, it just goes on, as in Talk.
            self._first_audio = misheard
            self._play_clip("sorry")
        self._idle_if_quiet()

    def _answer(self, text: str, stat: dict[str, Any]) -> None:
        """Talk's interruptWith: cut what is playing, close the turn still writing, then the next turn."""
        turn = self._turn
        running = self._writing()
        heard = None
        if running or self._pacer.busy():
            # R9.2: the reply is cut where the caller cut in, and the model keeps only what was heard. A reply that
            # had played out (only a clip after it was still playing) keeps all of it.
            self._pacer.stop_reply()
            if turn is not None and not turn.played_all and turn.message_id is not None:
                heard = Heard(turn.message_id, self._pacer.heard())
            if self._said is not None and self._said.finished_at is None:
                self._said.finished_at = self._at()
        if running:
            self._switching = (text, stat, heard)
            self._close_turn()
            turn.task.add_done_callback(lambda _: self._post(_Switched()))
            return
        self._start_turn(text, stat, heard)

    def _switched(self) -> None:
        if self._switching is None:
            return
        text, stat, heard = self._switching
        self._switching = None
        if self._accepted:     # said while the reply was stopping: joined, as Talk does
            text = " ".join([text, *(said for said, _ in self._accepted)])
            self._accepted.clear()
        self._start_turn(text, stat, heard)

    def _start_turn(self, text: str, stat: dict[str, Any], heard: Heard | None) -> None:
        if self._stopped:
            return
        self._idle_stage = 0
        self._disarm_idle()
        self._pacer.new_reply()          # R7.1: before any of this turn's clips
        if self._hearing:
            self._pacer.pause()          # R7.2: a speech run still open holds the new reply too
        turn = _Turn(text, heard, stat)
        self._turn = turn
        self._stats.append(stat)
        self._first_audio = stat
        turn.task = asyncio.create_task(self._run_turn(turn))

    def _turn_over(self, turn: _Turn) -> None:
        if turn is not self._turn or turn.closed:
            return
        turn.finished = True
        if turn.error == "budget_exhausted":
            self._end("budget", "closed", cut=False)
            return
        if not self._pacer.busy():
            self._quiet_now()

    def _drained(self) -> None:
        if self._pacer.busy():
            return                       # more was queued since
        if self._writing():
            return                       # R9.4: a slow voice ran dry mid-reply; the reply isn't over
        self._quiet_now()

    def _quiet_now(self) -> None:
        """Everything the line had to say has gone out."""
        if self._turn is not None and self._turn.finished:
            self._turn.played_all = True
        if self._said is not None and self._said.finished_at is None:
            self._said.finished_at = self._at()
        self._idle_if_quiet()

    def _writing(self) -> bool:
        """The latest turn is still at work: claiming, reading the model, or voicing."""
        turn = self._turn
        return turn is not None and not turn.finished and not turn.closed

    def _busy(self) -> bool:
        """A reply is being written, or something is still playing."""
        return self._writing() or self._pacer.busy()

    # --- hearing ---

    async def _receive(self) -> None:
        hearing = self._deps.hearing(self._deps.settings, self._deps.cfg.max_utterance_s)
        silence_s = self._deps.settings["silence_ms"] / 1000
        async for event in self._media.events():
            if isinstance(event, HungUp):
                self._lost()
                self._post(event)
                return
            if isinstance(event, Digit):
                logger.info("phone call %s: a key was pressed; keys are ignored", self._session_id)
                continue
            for heard in hearing.push(event.pcm):
                if isinstance(heard, SpeechEnded):
                    # The VAD calls a turn over only after its silence; the caller stopped that long before.
                    ended = self._at()
                    self._post(_Clip(heard.clip, ended, _stat(round(max(0.0, ended - silence_s), 3))))
                else:
                    self._post(heard)
        self._lost()                     # a media whose events just end has ended all the same
        self._post(HungUp("caller"))

    def _drain(self) -> None:
        """Transcribe the clips one at a time, in order."""
        if self._transcribing is not None or not self._waiting or self._stopped or self._ending:
            return
        self._transcribing = self._spawn(self._transcribe(self._waiting.popleft()))

    async def _transcribe(self, clip: _Clip) -> None:
        try:
            transcript = await self._deps.transcribe(_wav(clip.samples), self._key)
        except Exception as exc:
            logger.warning("phone call %s: the recognizer failed on a clip (%s)", self._session_id,
                           type(exc).__name__)
            transcript = None
        self._post(_Transcribed(clip, transcript))

    # --- a turn ---

    async def _run_turn(self, turn: _Turn) -> None:
        try:
            await self._produce(turn)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("phone call %s: a turn failed", self._session_id)
            turn.error = turn.error or "internal"
        if turn.closed:
            return
        if turn.error != "budget_exhausted" and (turn.error or not turn.ends):
            self._turn_clip(turn, "sorry")     # a failed answer, or one that said nothing (spec §5.11)
        error = turn.error
        if error is None and turn.voice_failed:
            error = "voice"                    # a sentence was skipped
        if error is None and not turn.ends:
            error = "empty"                    # the model wrote nothing that could be said
        turn.stat["error"] = error
        self._post(_TurnOver(turn))

    async def _produce(self, turn: _Turn) -> None:
        """Claim, read the model's events, voice the sentences; returns when the last sentence is in the pacer."""
        line = self._line
        req = TurnRequest(user_id=line.service_user_id, session_id=self._session_id, model_id=line.model_id,
                          text=turn.text, attachments=(), settings=CallSettings(think="off"), doc_id=None,
                          timezone=None, project_id=line.project_id, spoken=True, channel="phone",
                          profile_id=line.profile_id)
        pieces: asyncio.Queue[Piece | None] = asyncio.Queue()
        voice = asyncio.create_task(self._voice(turn, pieces))
        try:
            claim = await self._claim(turn, req)
            if claim is not None:
                await self._read(turn, req, claim, pieces)
            pieces.put_nowait(None)
            await voice
        finally:
            helpers = [task for task in (voice, turn.checking) if task is not None]
            for task in helpers:
                task.cancel()
            await asyncio.gather(*helpers, return_exceptions=True)

    async def _claim(self, turn: _Turn, req: TurnRequest) -> TurnClaim | None:
        begin = asyncio.ensure_future(self._deps.begin(
            session_id=self._session_id, user_id=self._line.service_user_id, model_id=self._line.model_id,
            text=turn.text, attachments=[], new_session_pins=None, scope_project_id=self._line.project_id,
            heard=turn.heard))
        try:
            claim = await asyncio.shield(begin)
        except asyncio.CancelledError:
            # Cut while the claim was being made. It is one transaction, so it lands all the same: give it back,
            # or this chat keeps a reply stuck "streaming".
            claim = await self._landed(begin)
            if claim is not None:
                self._turns += 1
                await self._give_back(req, claim)
            raise
        except Exception as exc:
            logger.warning("phone call %s: the turn could not start (%s)", self._session_id, type(exc).__name__)
            turn.error = "begin"
            return None
        self._turns += 1
        turn.message_id = claim.assistant_message_id
        return claim

    @staticmethod
    async def _landed(begin: asyncio.Future) -> TurnClaim | None:
        try:
            return await begin
        except Exception:
            return None

    async def _give_back(self, req: TurnRequest, claim: TurnClaim) -> None:
        """Release a claim no turn will use, the way run_turn does: its opening reads the profile and says "start"
        (no model call), and closing it there runs its own finally, which saves the empty reply as aborted and frees
        the chat. The orchestrator's abandon_turn does the same, but a call only has `run`."""
        events = self._deps.run(req, claim)
        try:
            await anext(events, None)
        except Exception:
            logger.warning("phone call %s: giving an unused claim back failed", self._session_id)
        finally:
            await events.aclose()

    async def _read(self, turn: _Turn, req: TurnRequest, claim: TurnClaim, pieces: asyncio.Queue) -> None:
        splitter = SentenceSplitter(self._deps.settings["first_clause_chars"])
        wrote = False
        events = self._deps.run(req, claim)
        try:
            try:
                async for event in events:
                    kind = event.get("type")
                    if kind == "text-start" and wrote:
                        # A later step's text is a new paragraph: run_turn saves "\n\n" there (and Talk feeds it), so
                        # the sentences' ends, and with them `heard`, count the reply as it is saved.
                        self._cut(turn, splitter.feed("\n\n"), pieces)
                    elif kind == "text-delta" and event.get("delta"):
                        wrote = True
                        if turn.stat["first_text_at"] is None:
                            turn.stat["first_text_at"] = self._at()
                        self._stop_checking(turn)
                        self._cut(turn, splitter.feed(event["delta"]), pieces)
                    elif kind == "text-end":
                        # Words written before a tool are spoken now, not after the answer (Talk's trap, fixed).
                        self._cut(turn, splitter.end_step(), pieces)
                    elif kind == "tool-input-available":
                        self._tool_started(turn)
                    elif kind == "error":
                        turn.error = str(event.get("code") or "error")
                        logger.info("phone call %s: the turn ended with %s", self._session_id, turn.error)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("phone call %s: reading the turn failed", self._session_id)
                turn.error = turn.error or "internal"
            self._cut(turn, splitter.flush(), pieces)
        finally:
            # Closed, the generator runs run_turn's finally: _finalize saves the reply and gives the claim back.
            await events.aclose()

    def _cut(self, turn: _Turn, new: list[Piece], pieces: asyncio.Queue) -> None:
        for piece in new:
            if turn.stat["first_sentence_at"] is None:
                turn.stat["first_sentence_at"] = self._at()
            turn.pieces += 1
            pieces.put_nowait(piece)

    def _tool_started(self, turn: _Turn) -> None:
        if not turn.filled and turn.pieces == 0:
            turn.filled = True
            if self._turn_clip(turn, "filler", self._fillers):
                self._fillers += 1
        if not turn.checked and turn.checking is None:
            turn.checking = asyncio.create_task(self._checking(turn))

    async def _checking(self, turn: _Turn) -> None:
        await self._deps.sleep(self._deps.cfg.checking_s)
        turn.checking = None
        if not turn.checked:
            turn.checked = True
            self._turn_clip(turn, "checking")

    @staticmethod
    def _stop_checking(turn: _Turn) -> None:
        if turn.checking is not None:
            turn.checking.cancel()
            turn.checking = None

    async def _voice(self, turn: _Turn, pieces: asyncio.Queue) -> None:
        """The reply's sentences in the line's voice, in order, into the pacer. The next one is voiced once no more
        than AHEAD of the voiced ones are unheard: so AHEAD wait beyond the one playing, as in Talk, and one more once
        that one is more than half heard (the pacer's rule). A reply cut short wastes little voice."""
        while (piece := await pieces.get()) is not None:
            while sum(end > self._pacer.heard() for end in turn.ends) > AHEAD:
                await self._deps.sleep(FRAME_S)
            if turn.closed or self._stopped:
                return
            try:
                pcm = await self._deps.render(piece.text)
            except Exception as exc:
                logger.warning("phone call %s: the voice failed on a sentence (%s); it is skipped", self._session_id,
                               type(exc).__name__)
                pcm = b""
            if turn.closed or self._stopped:
                return
            if not pcm:
                turn.voice_failed = True   # skipped, as in Talk
                continue
            self._play(pcm, end=piece.end)
            turn.ends.append(piece.end)
            turn.said.sentences.append(piece.text)
            self._said = turn.said

    # --- the line speaking ---

    async def _send(self, frame: bytes) -> None:
        if self._first_audio is not None and frame != SILENCE:
            self._first_audio["first_audio_at"] = self._at()
            self._first_audio = None
        await self._media.send(frame)

    def _play(self, pcm: bytes, *, end: int | None = None) -> None:
        self._disarm_idle()               # R9.4: any sound stops the idle clock
        self._pacer.play(pcm, end=end)

    def _play_clip(self, kind: str, n: int = 0) -> bool:
        pcms = self._clips.get(kind) or []
        if not pcms:
            logger.warning("phone call %s: the line has no %s clip", self._session_id, kind)
            return False
        self._play(pcms[n % len(pcms)])
        return True

    def _turn_clip(self, turn: _Turn, kind: str, n: int = 0) -> bool:
        if turn.closed or self._stopped:
            return False
        return self._play_clip(kind, n)

    # --- the idle clock ---

    def _quiet(self) -> bool:
        """Nothing is being said, heard or answered."""
        return not (self._ending or self._stopped or self._hearing or self._transcribing or self._waiting
                    or self._accepted or self._switching or self._busy())

    def _idle_if_quiet(self) -> None:
        """R9.4: the idle clock runs only once the turn is over and everything it said has gone out. A caller's
        sound that came to nothing (noise, echo) doesn't restart it: it carries on from where it was."""
        if not self._quiet() or self._idle_task is not None:
            return
        if self._idle_at is None:
            self._idle_at = self._deps.clock() + self._deps.cfg.idle_s
        self._idle_gen += 1
        self._idle_task = self._spawn(self._idle_timer(self._idle_gen, self._idle_at))

    async def _idle_timer(self, gen: int, at: float) -> None:
        await self._deps.sleep(max(0.0, at - self._deps.clock()))
        self._post(_Idle(gen))

    def _suspend_idle(self) -> None:
        """The caller may be talking: the idle clock waits, and keeps its deadline."""
        self._idle_gen += 1
        if self._idle_task is not None:
            self._idle_task.cancel()
            self._idle_task = None

    def _disarm_idle(self) -> None:
        self._suspend_idle()
        self._idle_at = None

    def _idle(self, gen: int) -> None:
        if gen != self._idle_gen:
            return
        self._idle_task = None
        if not self._quiet():
            return
        self._idle_at = None
        if self._idle_stage == 0:
            self._idle_stage = 1
            self._play_clip("still")
            self._idle_if_quiet()          # without a `still` clip, the clock runs on to goodbye
        else:
            self._end("idle", "goodbye", cut=False)

    # --- endings ---

    async def _limit(self) -> None:
        await self._deps.sleep(max(0.0, self._line.max_call_s - (self._deps.clock() - self._t0)))
        self._post(_Limit())

    def _end(self, reason: str, clip: str, *, cut: bool) -> None:
        """The call ends on our side. The ending clip goes after anything cut (stop_reply clears clips too) and
        nobody can interrupt it (R9.5); `_converse` returns once it has gone out."""
        logger.info("phone call %s: ending (%s)", self._session_id, reason)
        self._ending = reason
        self._disarm_idle()
        self._hearing = False
        self._first_audio = None
        if self._transcribing is not None:
            self._transcribing.cancel()
            self._transcribing = None
        self._waiting.clear()
        self._accepted.clear()
        self._switching = None
        if cut:
            self._pacer.stop_reply()
            self._close_turn()
        self._pacer.resume()
        self._play_clip(clip)
        if not self._pacer.busy():
            self._post(_Drained())

    def _close_turn(self) -> None:
        """Cut the latest turn: once, so its generator's close (and run_turn's _finalize) is never cancelled twice."""
        turn = self._turn
        if turn is None or turn.closed:
            return
        turn.closed = True
        if turn.task is not None and not turn.task.done():
            turn.task.cancel()

    def _lost(self) -> None:
        """The caller is gone: nothing more is asked of the model, the voice or the recognizer (review focus 2)."""
        self._stopped = True
        self._disarm_idle()
        self._close_turn()
        self._waiting.clear()
        if self._transcribing is not None:
            self._transcribing.cancel()

    async def _finish(self, pacer: asyncio.Task) -> None:
        """R9.6: everything stops, and the pacer has stopped before the hang-up begins, so no audio follows the 0x00.
        The hang-up is never cancelled half-way. Last, the turn's generator has closed: the reply saved, the claim
        given back."""
        self._lost()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(pacer, return_exceptions=True)
        hang_up = asyncio.ensure_future(self._media.hang_up())
        hang_up.add_done_callback(lambda t: t.cancelled() or t.exception())   # read, even if no one waits for it
        try:
            await asyncio.shield(hang_up)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("phone call %s: hanging up failed (%s)", self._session_id, type(exc).__name__)
        turn = self._turn.task if self._turn is not None else None
        await asyncio.gather(*self._tasks, *([turn] if turn else []), return_exceptions=True)

    # --- plumbing ---

    def _pacer_stopped(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if not isinstance(exc, ConnectionError):
            logger.error("phone call %s: the pacer stopped", self._session_id, exc_info=exc)
        self._lost()
        self._post(_LineDown())

    def _receiver_stopped(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            logger.error("phone call %s: hearing the caller failed", self._session_id, exc_info=task.exception())
            self._post(_Deaf())

    def _spawn(self, coro: Coroutine, done: Callable[[asyncio.Task], None] | None = None) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        if done is not None:
            task.add_done_callback(done)
        return task

    def _post(self, event: object) -> None:
        self._inbox.put_nowait(event)

    def _at(self) -> float:
        return round(self._deps.clock() - self._t0, 3)
