"""The app's name, tagline and copyright come from the environment (profile library spec, "The app's name")."""
import httpx
from fastapi import FastAPI
from httpx import ASGITransport

from server.chat import prompt
from server.routers import app_info
from server.services import app_identity

DEFAULT = app_identity.Identity("Natural Reader", "an app for reading and listening to documents", "")


def test_unset_means_the_built_in_name_and_no_copyright():
    assert app_identity.load({}) == DEFAULT
    assert app_identity.load({"APP_NAME": "  ", "APP_TAGLINE": "\n", "APP_COPYRIGHT": " "}) == DEFAULT


def test_values_are_one_clean_line_within_their_limits():
    got = app_identity.load({"APP_NAME": "Acme\nDocs\x07 " + "x" * 500, "APP_TAGLINE": "t" * 500,
                             "APP_COPYRIGHT": "© 2026  Example\tOrg " + "y" * 500})
    assert got.name.startswith("Acme Docs x") and len(got.name) == 40 and "\n" not in got.name
    assert len(got.tagline) == 120
    assert got.copyright.startswith("© 2026 Example Org y") and len(got.copyright) == 80


def test_the_first_line_is_what_it_was_by_default():
    assert prompt.first_line(False, DEFAULT) == (
        "You are the assistant in Natural Reader, an app for reading and listening to documents.")
    assert prompt.first_line(True, DEFAULT) == (
        "You are working in Natural Reader, an app for reading and listening to documents.")


def test_the_rules_carry_the_deployments_name(monkeypatch):
    monkeypatch.setattr(app_identity, "current", lambda: app_identity.Identity("Acme Docs", "a library for contracts", ""))
    assert prompt.system_rules(None, [], has_pins=False).startswith(
        "You are the assistant in Acme Docs, a library for contracts.")
    assert "\nYou are working in Acme Docs, a library for contracts." in prompt.system_rules(
        None, [], has_pins=False, profile="You are Ada.")


async def test_the_route_answers_before_sign_in(monkeypatch):
    monkeypatch.setattr(app_identity, "current", lambda: app_identity.Identity("Acme Docs", "a library", "© 2026 Example Org"))
    app = FastAPI()
    app.include_router(app_info.router)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/v1/app")
    assert (r.status_code, r.json()) == (200, {"name": "Acme Docs", "tagline": "a library", "copyright": "© 2026 Example Org"})
