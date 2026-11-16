"""Helper model phase 1, Task 2: services/helper.py, the one caller of the helper model (spec 2026-11-15 §4.2, §4.3).
One capped call with a fixed brief; failures are typed; usage is recorded against the person who caused it; logs
carry lengths and kinds, never text."""
from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from server.auth.users import resolve_or_provision_user, set_status
from server.llm.router import Router, load_provider_configs
from server.llm.types import Usage
from server.services import helper
from server.tests.chat_harness import shim_pool
from server.tests.test_chat_reader import FakeRouter

SECRET = "the quarterly numbers nobody may see"
OLLAMA_ENV = {"OLLAMA_URL": "http://localhost:11434"}


def _cfg(model="ollama:gemma4:e4b", timeout_s=5.0):
    return helper.HelperConfig(model=model, timeout_s=timeout_s, input_chars=6000, num_ctx=None, local=True)


@pytest.fixture
def router(monkeypatch):
    def install(fake):
        monkeypatch.setattr(helper, "get_router", lambda: fake)
        return fake
    return install


async def _user(conn, sub="helper-user"):
    u = await resolve_or_provision_user(conn, iss="i", sub=sub, email=f"{sub}@x.io")
    await set_status(conn, u["id"], "active")
    return str(u["id"])


async def _usage(conn, user_id):
    cur = await conn.execute("SELECT model, kind, prompt_tokens, eval_tokens, requests, estimated_tokens "
                             "FROM inference_usage WHERE user_id = %s", (user_id,))
    return await cur.fetchall()


async def test_off_raises_off(router):
    fake = router(FakeRouter("never"))
    with pytest.raises(helper.HelperError) as e:
        await helper.ask(_cfg(model=""), "system", "user", max_output_chars=600, user_id=None)
    assert e.value.kind == "off" and fake.calls == []


async def test_reply_is_returned_with_a_capped_cool_call(router):
    fake = router(FakeRouter('{"tags": ["a"]}'))
    out = await helper.ask(_cfg(), "the brief", "the text", max_output_chars=600, user_id=None)
    assert out == '{"tags": ["a"]}'
    model, messages, settings = fake.calls[0]
    assert model == "ollama:gemma4:e4b"
    assert [(m.role, m.content) for m in messages] == [("system", "the brief"), ("user", "the text")]
    assert settings.think == "off" and settings.num_predict == 300 and settings.temperature == 0.2


async def test_timeout_raises_timeout(router):
    router(FakeRouter("late", delay=0.5))
    with pytest.raises(helper.HelperError) as e:
        await helper.ask(_cfg(timeout_s=0.05), "s", "u", max_output_chars=600, user_id=None)
    assert e.value.kind == "timeout"


async def test_provider_failure_is_provider_error_and_logs_no_text(router, caplog):
    router(FakeRouter(RuntimeError(f"upstream said: {SECRET}")))
    caplog.set_level(logging.DEBUG)
    with pytest.raises(helper.HelperError) as e:
        await helper.ask(_cfg(), "s", SECRET, max_output_chars=600, user_id=None)
    assert e.value.kind == "provider_error"
    assert SECRET not in caplog.text and "RuntimeError" in caplog.text


async def test_usage_recorded_against_user(db_conn, monkeypatch, router):
    shim_pool(monkeypatch, db_conn, helper)
    uid = await _user(db_conn)
    router(FakeRouter("ok", usage=Usage(120, 30)))
    await helper.ask(_cfg(), "s", "u", max_output_chars=600, user_id=uid)
    assert await _usage(db_conn, uid) == [("ollama:gemma4:e4b", "helper", 120, 30, 1, 0)]


async def test_usage_is_estimated_when_the_provider_sends_none(db_conn, monkeypatch, router):
    shim_pool(monkeypatch, db_conn, helper)
    uid = await _user(db_conn, "helper-estimate")
    router(FakeRouter("x" * 40))
    await helper.ask(_cfg(), "s" * 400, "u" * 400, max_output_chars=600, user_id=uid)
    [(model, kind, prompt, out, requests, estimated)] = await _usage(db_conn, uid)
    assert (kind, prompt, out, requests) == ("helper", 200, 10, 1) and estimated == 210


async def test_a_call_that_produced_nothing_is_not_charged(db_conn, monkeypatch, router):
    shim_pool(monkeypatch, db_conn, helper)
    uid = await _user(db_conn, "helper-nothing")
    router(FakeRouter(RuntimeError("refused")))
    with pytest.raises(helper.HelperError):
        await helper.ask(_cfg(), "s", "u", max_output_chars=600, user_id=uid)
    assert await _usage(db_conn, uid) == []


async def test_over_budget_makes_no_call(db_conn, monkeypatch, router):
    shim_pool(monkeypatch, db_conn, helper)
    uid = await _user(db_conn, "helper-broke")
    monkeypatch.setattr(helper, "_deployment_budget", lambda: 100)
    from server.services import inference_budget
    await inference_budget.record_usage(db_conn, uid, 90, 20, model="ollama:x", kind="chat", estimated=False)
    fake = router(FakeRouter("never"))
    with pytest.raises(helper.HelperError) as e:
        await helper.ask(_cfg(), "s", "u", max_output_chars=600, user_id=uid)
    assert e.value.kind == "over_budget" and fake.calls == []


async def test_env_ranges_fall_back_to_defaults(db_conn, monkeypatch):
    monkeypatch.setattr(helper, "get_router", lambda: Router(load_provider_configs(OLLAMA_ENV), httpx.AsyncClient()))
    env = {**OLLAMA_ENV, "HELPER_MODEL": "gemma4:e4b", "HELPER_TIMEOUT_S": "3", "HELPER_INPUT_CHARS": "99999999"}
    cfg = await helper.load_config(db_conn, env)
    assert (cfg.timeout_s, cfg.input_chars) == (120.0, 6000)
    cfg = await helper.load_config(db_conn, {**env, "HELPER_TIMEOUT_S": "30", "HELPER_INPUT_CHARS": "800"})
    assert (cfg.timeout_s, cfg.input_chars) == (30.0, 800)


async def test_load_config_names_the_canonical_model_and_its_place(db_conn, monkeypatch):
    monkeypatch.setattr(helper, "get_router", lambda: Router(load_provider_configs(OLLAMA_ENV), httpx.AsyncClient()))
    cfg = await helper.load_config(db_conn, {**OLLAMA_ENV, "HELPER_MODEL": "gemma4:e4b"})
    assert cfg.model == "ollama:gemma4:e4b" and cfg.local is True and cfg.num_ctx == 16384
    cloud = await helper.load_config(db_conn, {**OLLAMA_ENV, "HELPER_MODEL": "ollama:gpt-oss:120b-cloud"})
    assert cloud.local is False and cloud.num_ctx is None
    off = await helper.load_config(db_conn, OLLAMA_ENV)
    assert off.model == "" and off.on is False
