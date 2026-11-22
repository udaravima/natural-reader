"""A real call through the phone service, end to end, with nothing faked on the way (spec §7, "End to end").

`scripts/phone_fake_pbx.py` (the script a person runs) connects to the real AudioSocket listener as Asterisk would and
asks a recorded question. The service runs its real call loop: Silero hears the question, Whisper (the local engine
the deployment would use) transcribes it, the chat engine answers from the line's project with a real model, and
Kokoro speaks the answer. The test then reads the recording and the database.

It is skipped unless PHONE_E2E_MODEL names a model the machine can reach, as a model id the router understands, e.g.

    PHONE_E2E_MODEL=ollama:gemma4:e4b python -m pytest server/tests/test_phone_e2e.py -q -s

Ollama is looked for at OLLAMA_URL (default http://127.0.0.1:11434). It also needs the Whisper model the speech
settings choose and the Kokoro files, so on a machine that has none of them it fails rather than skips: the point is
the real path. A CPU-only machine takes minutes. Run it from the repository root with the scratch database's
environment, like every backend test."""
import asyncio
import io
import os
import re
import sys
import wave
from pathlib import Path

import httpx
import numpy as np
import pytest

from server.llm import router as llm_router
from server.phone import clips as phone_clips, service, store, vad
from server.phone.config import PhoneConfig
from server.phone.store import LineVoice
from server.services import chat_options, doc_search, embeddings
from server.services.embeddings import current_profile
from server.speech import service as speech_service
from server.tests import seed
from server.tests.chat_harness import member
from server.tests.test_phone_service import pool, scratch_url  # noqa: F401 — the module's real pool fixtures

MODEL = os.environ.get("PHONE_E2E_MODEL", "").strip()
needs_model = pytest.mark.skipif(not MODEL, reason="set PHONE_E2E_MODEL (e.g. ollama:gemma4:e4b) to run a real call")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "server" / "tests" / "fixtures" / "phone"
DOC = "e" * 64
QUESTION = "What time does the library open on Saturday?"
GREETING = "Hello, you've reached the library line. I'm an automated assistant. How can I help?"
FIRST_SOUND = re.compile(r"Question 1: first sound (\d+\.\d\d) s after it ended")
SOUND = re.compile(r"Question 1: the answer held (\d+\.\d) s of sound")
RECORDING = re.compile(r"Recorded [\d.]+ s of what came back: (.+\.wav)")
STOP_WORDS = {"that", "this", "with", "from", "have", "what", "your", "will", "about", "there", "their", "which",
              "would", "like", "only", "the", "and", "for", "you", "are", "can", "but", "not", "our", "its", "was"}
NUMBERS = {w: str(n) for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())}
NUMBERS.update({"thirty": "30", "forty": "40", "fifty": "50"})
CALL_TIMEOUT_S = 900          # a CPU-only machine hears, thinks and speaks slowly


async def library_project(conn):
    """A project holding one indexed document that answers question1.wav ("What time does the library open on
    Saturday?"). Returns (the owner, the project id)."""
    owner = await member(conn, f"owner-{os.getpid()}")
    project = await seed.make_project(conn, owner.user_id, "Library")
    await seed.seed_doc(conn, DOC, owner.user_id, file_name="Opening hours.md", state="indexed")
    await conn.execute("UPDATE documents SET page_count = 1, embedding_profile = %s WHERE doc_id = %s",
                       (current_profile(), DOC))
    # Embedded with the app's own embedder (the local model the test's environment names), as indexing would, so
    # the search tool can find it: search_chunks only ever returns chunks that have a vector.
    text = ("Opening hours. The library is open Monday to Friday from 8 am to 6 pm, on Saturday from 9 am to "
            "1 pm, and closed on Sunday.")
    embedder = await embeddings.current_embedder()
    vector = (await embeddings.embed_documents([text], embedder))[0]
    assert vector is not None, "the embedding model did not answer"
    await conn.execute(
        "INSERT INTO doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
        "VALUES (%s, 0, 1, 'page', %s, 'h0', %s::vector, %s)",
        (DOC, text, "[" + ",".join(str(x) for x in vector) + "]", embedder.model))
    await seed.place_doc(conn, project, DOC, owner.user_id, file_name="Opening hours.md")
    return owner, project


@needs_model
async def test_a_real_call_is_greeted_heard_and_answered(pool, tmp_path, monkeypatch):  # noqa: F811
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0))
    router = llm_router.Router(llm_router.load_provider_configs(
        {"OLLAMA_URL": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")}), client)
    monkeypatch.setattr(service, "get_router", lambda: router)
    # The embedding model is a local one, named here so the chunk and the question's search use the same.
    monkeypatch.setenv("OLLAMA_URL", os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    monkeypatch.setenv("EMBEDDING_MODEL", os.environ.get("PHONE_E2E_EMBEDDING_MODEL", "nomic-embed-text"))
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    await embeddings.start_client()
    pbx = None
    svc = service.PhoneService(PhoneConfig(listen="127.0.0.1:0", allow=(), pbx_address="127.0.0.1:0"))
    try:
        async with pool.connection() as conn:
            owner, project = await library_project(conn)
            line = await store.create_line(
                conn, owner, name="Library", profile_id=None, project_id=project, model_id=MODEL,
                voice=LineVoice("kokoro", "af_heart", None), greeting=GREETING)
            line = await store.update_line(conn, owner, line.id, enabled=True)
            greeting_clip = await phone_clips.render_clips(line)
            await store.save_clips(conn, line.id, greeting_clip)
        async with pool.connection() as conn:       # the search the model's tool makes must find the hours
            found = await doc_search.search_chunks(
                conn, DOC, await embeddings.embed_query(QUESTION), 3, QUESTION)
        assert found and "1 pm" in found[0]["text"], found
        greeting_pcm = next(c.pcm for c in greeting_clip if c.kind == "greeting")

        await asyncio.to_thread(vad._session)       # load Silero before the call, as start() does
        await svc.start()
        assert svc.status()["listening"]
        address = svc.status()["pbx_address"]

        pbx = await asyncio.create_subprocess_exec(
            sys.executable, str(ROOT / "scripts" / "phone_fake_pbx.py"), line.id, str(FIXTURES / "question1.wav"),
            "--address", address, "--out", str(tmp_path), "--quiet-s", "5", "--answer-timeout", str(CALL_TIMEOUT_S),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(pbx.communicate(), CALL_TIMEOUT_S)
        printed = out.decode()
        print("\n" + printed)
        assert pbx.returncode == 0, printed

        # The greeting comes first, then (after the question) an answer.
        recording = RECORDING.search(printed).group(1)
        with wave.open(recording, "rb") as w:
            assert w.getframerate() == 8000
            heard = w.readframes(w.getnframes())
        # Silence goes down the line from connect while the call is checked and opened (final fix F4: Asterisk drops a
        # call that gets no audio for 2 s), then the greeting. Whole silent frames are stripped from the front of both.
        lead = silent_lead(heard)
        assert lead >= 320, "no silence came before the greeting: the keep-alive didn't run"
        heard, greeting_pcm = heard[lead:], greeting_pcm[silent_lead(greeting_pcm):]
        assert heard[:len(greeting_pcm) - 320] == greeting_pcm[:-320], "the first thing heard is not the greeting"
        found_first, found_sound = FIRST_SOUND.search(printed), SOUND.search(printed)
        assert found_first and found_sound, printed
        first_sound, sound_s = float(found_first.group(1)), float(found_sound.group(1))
        print(f"first sound {first_sound:.2f} s after the question ended ({MODEL}); the answer held {sound_s:.1f} s of sound")
        assert first_sound > 0 and sound_s >= 1.0, "the answer was not spoken"

        # The call is recorded: one turn, ended by the caller, with the question and an answer as its transcript.
        async def row():
            async with pool.connection() as conn:
                return (await store.list_calls(conn, line.id))[0]
        for _ in range(100):
            call = await row()
            if call["end_reason"]:
                break
            await asyncio.sleep(0.1)
        assert call["end_reason"] == "caller" and call["turns"] == 1, call
        async with pool.connection() as conn:
            transcript = (await store.get_call(conn, call["id"]))["messages"]
        said = [m["content"].lower() for m in transcript if m["role"] == "user"]
        assert len(said) == 1 and "library" in said[0] and "saturday" in said[0], said
        assert [m["role"] for m in transcript] == ["assistant", "user", "assistant"]
        assert transcript[-1]["content"].strip(), "the answer is empty"
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT tool_calls FROM chat_messages WHERE session_id = "
                                     "(SELECT session_id FROM phone_calls WHERE id = %s) AND tool_calls IS NOT NULL",
                                     (call["id"],))
            print("tool calls:", [r[0] for r in await cur.fetchall()])
        print("reply:", transcript[-1]["content"])
        # Search works (checked above), so a reply that doesn't give Saturday's opening time is a wrong answer.
        assert "9" in content_words(transcript[-1]["content"]), transcript[-1]["content"]
        assert any(isinstance(t.get("first_audio_at"), (int, float)) for t in call["stats"]), call["stats"]

        # What came back, heard by the same recognizer, is the reply the transcript holds.
        wav = io.BytesIO()
        with wave.open(wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(sound_only(heard[len(greeting_pcm):]))
        async with pool.connection() as conn:
            options = await chat_options.load_chat_options(conn)
        spoken = await speech_service.transcribe_for("e2e-check", wav.getvalue(), "audio/wav", "en",
                                                     engine=speech_service.engine_for(options, "en"))
        print(f"the answer, heard back: {spoken.text!r}")
        shared = content_words(spoken.text) & content_words(transcript[-1]["content"])
        assert len(shared) >= 2, (shared, spoken.text, transcript[-1]["content"])
    finally:
        if pbx is not None and pbx.returncode is None:
            pbx.kill()
            await pbx.wait()
        await svc.stop()
        await router.aclose()
        await embeddings.stop_client()



def silent_lead(pcm: bytes) -> int:
    """How many bytes of whole 320-byte frames of digital silence `pcm` starts with: what the keep-alive sends."""
    n = 0
    while pcm[n:n + 320] == b"\0" * 320:
        n += 320
    return n


def sound_only(pcm: bytes, threshold: float = 100.0) -> bytes:
    """The 20 ms frames of `pcm` that carry sound (RMS of 32768 at least `threshold`, as the fake PBX counts them), so
    the answer is heard back without the silence around it: a short clip, and nothing for Whisper to invent words from."""
    frames = [pcm[i:i + 320] for i in range(0, len(pcm) - 319, 320)]
    return b"".join(f for f in frames if np.sqrt(np.mean(np.frombuffer(f, dtype="<i2").astype(np.float64) ** 2)) >= threshold)


def content_words(text: str) -> set[str]:
    """The words of `text` that carry meaning: numbers (number words become numerals) and words of three letters or
    more, less the common filler words."""
    words = (NUMBERS.get(w, w) for w in re.findall(r"[a-z0-9]+", text.lower()))
    return {w for w in words if (w.isdigit() or len(w) >= 3) and w not in STOP_WORDS}


def test_a_time_only_answer_shares_words_with_its_spoken_form():
    shared = content_words("Saturday, 9 am to 1 pm.") & content_words("On Saturday it opens nine am to one pm")
    assert len(shared) >= 2, shared
    assert {"saturday", "9", "1"} <= shared
