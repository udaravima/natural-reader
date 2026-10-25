"""Admin console v2, Task 4: the Docling limits: how the console narrows what the environment allows, and the
rules a conversion request is held to (the route itself is pinned in test_convert_limits.py)."""
from __future__ import annotations

import logging

import pytest

from server.auth.users import SEED_ADMIN_ID
from server.services import console_settings
from server.services.console_settings import SettingError
from server.services.docling_limits import (SECTION, ConvertLimits, Refused, check, info_rows, load_limits,
                                            may_convert, pages_requested, user_view)


@pytest.fixture(autouse=True)
def _fresh_warnings():
    console_settings._warned.clear()


async def _narrowed(db_conn, console, env):
    await console_settings.save(db_conn, SECTION, console, updated_by=SEED_ADMIN_ID, env=env)
    return {name: v.value for name, v in (await console_settings.load(db_conn, SECTION, env)).items()}


async def test_the_console_narrows_and_never_widens_the_environment(db_conn):   # Review Focus 5
    vals = await _narrowed(db_conn, {"presets": ["accurate", "standard"]}, {"DOCLING_PRESETS": "fast,standard"})
    assert vals["presets"] == ["standard"]


async def test_the_environment_alone_narrows_without_a_console_row(db_conn):
    vals = await _narrowed(db_conn, {}, {"DOCLING_PRESETS": "accurate, fast", "DOCLING_ALLOW_OCR": "no",
                                         "DOCLING_ALLOW_PICTURE_DESCRIPTION": "0", "DOCLING_MAX_PAGES": "40",
                                         "DOCLING_CONVERT_BY": "role"})
    assert vals == {"convert_by": "role", "presets": ["fast", "accurate"], "ocr": False,
                    "picture_description": False, "max_pages": 40}


async def test_the_defaults_allow_everything(db_conn):
    vals = await _narrowed(db_conn, {}, {})
    assert vals == {"convert_by": "readers", "presets": ["fast", "standard", "accurate"], "ocr": True,
                    "picture_description": True, "max_pages": 0}


@pytest.mark.parametrize("console, env, expected", [(0, 0, 0), (0, 50, 50), (100, 50, 50), (30, 50, 30), (30, 0, 30)])
def test_max_pages_takes_the_lower_limit(console, env, expected):
    assert SECTION.get("max_pages").narrow(console, env) == expected


@pytest.mark.parametrize("console, env, expected", [(True, True, True), (True, False, False), (False, True, False),
                                                    (False, False, False)])
def test_a_switch_is_on_only_when_both_sides_allow_it(console, env, expected):
    assert SECTION.get("ocr").narrow(console, env) is expected


@pytest.mark.parametrize("console, env, expected", [("readers", "readers", "readers"), ("role", "readers", "role"),
                                                    ("readers", "role", "role"), ("role", "role", "role")])
def test_role_wins_when_either_side_says_it(console, env, expected):
    assert SECTION.get("convert_by").narrow(console, env) == expected


@pytest.mark.parametrize("name, value, message", [
    ("presets", [], "Keep at least one preset."),
    ("ocr", "yes", "Must be true or false."),
    ("ocr", 1, "Must be true or false."),
    ("picture_description", "false", "Must be true or false."),
    ("max_pages", True, "Must be a whole number from 0 to 100000."),
    ("max_pages", 1.5, "Must be a whole number from 0 to 100000."),
    ("max_pages", "9", "Must be a whole number from 0 to 100000."),
    ("max_pages", -1, "Must be a whole number from 0 to 100000."),
    ("max_pages", 100001, "Must be a whole number from 0 to 100000."),
    ("convert_by", "admins", "Must be readers or role."),
    ("convert_by", 3, "Must be readers or role."),
])
async def test_a_refused_value_names_why_and_writes_nothing(db_conn, name, value, message):
    with pytest.raises(SettingError) as e:
        await console_settings.save(db_conn, SECTION, {name: value}, updated_by=SEED_ADMIN_ID, env={})
    assert (e.value.name, str(e.value)) == (name, message)
    cur = await db_conn.execute("SELECT 1 FROM app_settings WHERE key = 'documents'")
    assert await cur.fetchone() is None


async def test_presets_the_environment_allows_none_of_are_refused(db_conn):
    with pytest.raises(SettingError) as e:
        await console_settings.save(db_conn, SECTION, {"presets": ["accurate"]}, updated_by=SEED_ADMIN_ID,
                                    env={"DOCLING_PRESETS": "fast"})
    assert str(e.value) == "None of these presets is allowed by the server setting DOCLING_PRESETS."


@pytest.mark.parametrize("bad", [["turbo"], "fast", [1]])
async def test_presets_must_be_a_list_of_known_names(db_conn, bad):
    with pytest.raises(SettingError):
        await console_settings.save(db_conn, SECTION, {"presets": bad}, updated_by=SEED_ADMIN_ID, env={})


def test_presets_parse_to_a_json_list_in_the_fixed_order():
    # a list, not a tuple, so the audit line's before/after compare equal to the stored JSON
    assert SECTION.get("presets").parse(["accurate", "fast", "fast"], ["fast", "standard", "accurate"]) \
        == ["fast", "accurate"]


async def test_a_bad_environment_value_is_ignored_with_one_warning(db_conn, caplog):
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            vals = await _narrowed(db_conn, {}, {"DOCLING_MAX_PAGES": "lots", "DOCLING_CONVERT_BY": "admins"})
    assert vals["max_pages"] == 0 and vals["convert_by"] == "readers"
    assert len([r for r in caplog.records if "DOCLING_MAX_PAGES" in r.getMessage()]) == 1


async def test_load_limits_builds_the_tuple_form(db_conn):
    await console_settings.save(db_conn, SECTION, {"presets": ["standard"], "max_pages": 5},
                                updated_by=SEED_ADMIN_ID, env={})
    assert await load_limits(db_conn, {}) == ConvertLimits("readers", ("standard",), True, True, 5)


# ---------- the rules ----------

def test_check_order_and_messages():
    limits = ConvertLimits("role", ("fast",), False, False, 10)
    first = check(limits, {"preset": "accurate", "ocr": True}, is_admin=False, capabilities=frozenset({"reader"}),
                  pages=50)
    assert (first.option, first.message) == (
        "convert_by", "Only people your admin has given the Docling role can convert documents on this server.")
    r = check(limits, {"preset": "accurate", "ocr": True}, is_admin=False,
              capabilities=frozenset({"reader", "docling"}), pages=50)
    assert (r.option, r.message) == ("preset", "Your admin has turned off the Accurate preset.")


def test_check_walks_ocr_then_pictures_then_pages():
    limits = ConvertLimits("readers", ("standard",), False, False, 10)
    caps = frozenset({"reader"})
    kw = dict(is_admin=False, capabilities=caps)
    assert check(limits, {"preset": "standard", "ocr": True, "images": "describe"}, pages=50, **kw) == Refused(
        "ocr", "Your admin has turned off OCR for conversions.")
    assert check(limits, {"preset": "standard", "images": "describe"}, pages=50, **kw) == Refused(
        "picture_description", "Your admin has turned off picture descriptions for conversions.")
    assert check(limits, {"preset": "standard"}, pages=50, **kw) == Refused(
        "max_pages", "This server converts at most 10 pages at a time. Choose a page range of 10 pages or fewer.")
    assert check(limits, {"preset": "standard"}, pages=10, **kw) is None
    assert check(limits, {"preset": "standard"}, pages=None, **kw) is None
    assert check(ConvertLimits("readers", ("standard",), True, True, 0), {"preset": "standard"}, pages=9999,
                 **kw) is None


def test_options_default_like_the_request_model():
    # an options dict without keys is the request model's defaults: standard, no OCR, drop images
    limits = ConvertLimits("readers", ("standard",), False, False, 0)
    assert check(limits, {}, is_admin=False, capabilities=frozenset({"reader"}), pages=None) is None


@pytest.mark.parametrize("is_admin, caps, want", [(False, {"reader"}, False), (False, {"reader", "docling"}, True),
                                                  (True, {"reader"}, True)])
def test_role_mode_lets_role_holders_and_admins_in(is_admin, caps, want):
    limits = ConvertLimits("role", ("fast",), True, True, 0)
    assert may_convert(limits, is_admin=is_admin, capabilities=frozenset(caps)) is want


def test_readers_mode_lets_anyone_in():
    assert may_convert(ConvertLimits("readers", ("fast",), True, True, 0), is_admin=False,
                       capabilities=frozenset()) is True


@pytest.mark.parametrize("page_range, total, want", [
    ([3, 10], 100, 8), ([3, 10], None, 8), ([3, 500], 100, 98), (None, 120, 120), (None, None, None), ([1, 1], 0, 1)])
def test_pages_requested(page_range, total, want):
    assert pages_requested(page_range, total) == want


def test_user_view_is_what_the_dialog_needs():
    limits = ConvertLimits("role", ("fast", "standard"), True, False, 25)
    assert user_view(limits, is_admin=False, capabilities=frozenset({"reader"}), enabled=True) == {
        "enabled": True, "may_convert": False, "presets": ["fast", "standard"], "ocr": True,
        "picture_description": False, "max_pages": 25}
    assert user_view(limits, is_admin=False, capabilities=frozenset({"docling"}), enabled=False)["may_convert"] is True


def test_info_rows_show_the_servers_own_values():
    rows = {r["id"]: r for r in info_rows({"DOCLING_ENABLED": "true", "PDF_UPLOAD_MAX_MB": "80"})}
    assert rows["docling"] == {"id": "docling", "label": "Docling", "value": "On", "env": "DOCLING_ENABLED",
                               "source": "env"}
    assert (rows["pdf_upload_max"]["value"], rows["pdf_upload_max"]["source"]) == ("80 MB", "env")
    assert rows["pdf_upload_max"]["env"] == "PDF_UPLOAD_MAX_MB"
    assert (rows["text_upload_max"]["value"], rows["text_upload_max"]["source"]) == ("10 MB", "default")
    assert {r["id"]: r["value"] for r in info_rows({})}["docling"] == "Off"
