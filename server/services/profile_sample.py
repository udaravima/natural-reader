"""Try it (character profiles spec, "Hear a sample reply"): the profile being edited, unsaved, answers one question
with the chat model the admin picks, the way a chat with no document would put it to the model: the saved house
rules, the labelled backstory, the rules, the app's own rules, no tools. The reply is returned and stored nowhere.
Logs carry lengths only."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..chat import profile_text
from ..chat.context import today_line, today_text
from ..chat.prompt import system_rules
from . import app_identity, helper, profile_library
from .assistant_profile import PROFILE_MAX_CHARS, clean_profile
from .profile_draft import LONG_TIMEOUT_S

logger = logging.getLogger(__name__)

QUESTION_MAX_CHARS = 500
OUTPUT_CHARS = 2000
TEMPERATURE = 0.7   # as a chat would answer, not the helper's careful 0.2


async def system_message(conn, *, name: str, backstory: str, text: str, now: datetime | None = None) -> str:
    """What a chat with no document would send as its system message for this profile."""
    now = now or datetime.now(timezone.utc)
    house = await profile_library.house_rules(conn)
    backstory = clean_profile(backstory).strip()[:profile_library.BACKSTORY_MAX_CHARS]
    text = clean_profile(text).strip()[:PROFILE_MAX_CHARS]
    lead = profile_text.fill(profile_text.compose(house, backstory, text), date=today_text(now, "UTC"),
                             app=app_identity.current().name, profile=" ".join(name.split()))
    return system_rules(None, [], has_pins=False, today=today_line(now, "UTC"), profile=lead.text,
                        placed=lead.used, persona=bool(text))


async def sample(conn, *, name: str, backstory: str, text: str, question: str, model: str, user_id: str) -> str | None:
    """The model's answer, charged to `user_id`; None when it said nothing. Raises helper.HelperError and
    llm.router.UnknownModel."""
    system = await system_message(conn, name=name, backstory=backstory, text=text)
    cfg = await helper.config_for(conn, model)
    reply = await helper.ask(cfg, system, question.strip(), max_output_chars=OUTPUT_CHARS, user_id=user_id,
                             temperature=TEMPERATURE, timeout_s=LONG_TIMEOUT_S)
    answer = clean_profile(reply).strip()
    logger.info("profile sample: %d characters of profile, %d out", len(system), len(answer))
    return answer or None
