"""The Lotus Bay Hotel's pretend reservation system (an invented hotel; spec 2026-11-17-call-simulation).

Every price, availability and policy the agent may say comes from here. A night's price is the room's rate, plus
the peak surcharge between the peak dates; a room is free on a night unless that night is sold out for its type or
its confirmed bookings fill every room of the type."""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from server.calls.simtools import ToolError, tool

ROOM_TYPES = ["standard", "deluxe", "family", "executive"]
MAX_NIGHTS = 14
_DATE = {"type": "string", "description": "YYYY-MM-DD"}


def _day(value: str, what: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise ToolError(f"{what} must be a date as YYYY-MM-DD, such as 2026-12-12, not {value!r}") from None


def _nights(state: dict, check_in: str, check_out: str) -> list[date]:
    start, end = _day(check_in, "check_in"), _day(check_out, "check_out")
    today = date.fromisoformat(state["today"])
    if start < today:
        raise ToolError(f"{start.isoformat()} is in the past: today is {today.isoformat()}")
    if end <= start:
        raise ToolError("check_out must be after check_in")
    if (end - start).days > MAX_NIGHTS:
        raise ToolError(f"Bookings by phone are for up to {MAX_NIGHTS} nights; longer stays go to the sales team")
    return [start + timedelta(days=n) for n in range((end - start).days)]


def _price(state: dict, room_type: str, night: date) -> int:
    rate = state["rooms"][room_type]["rate"]
    peak = state.get("peak") or {}
    if peak and date.fromisoformat(peak["from"]) <= night <= date.fromisoformat(peak["to"]):
        rate = rate * (100 + peak["surcharge_percent"]) // 100
    return rate


def _said(night: date) -> str:
    return f"{night.day} {night:%B}"


def _why_not(state: dict, room_type: str, nights: list[date], guests: int, skip: str | None = None) -> str | None:
    room = state["rooms"][room_type]
    if guests > room["max_guests"]:
        return f"the {room['name']} takes up to {room['max_guests']} guests"
    for night in nights:
        for block in state.get("sold_out") or ():
            if block["room_type"] == room_type and \
                    date.fromisoformat(block["from"]) <= night < date.fromisoformat(block["to"]):
                return f"sold out on the night of {_said(night)}"
        taken = sum(1 for b in state["bookings"]
                    if b["room_type"] == room_type and b["status"] == "confirmed" and b["reference"] != skip
                    and date.fromisoformat(b["check_in"]) <= night < date.fromisoformat(b["check_out"]))
        if taken >= room["count"]:
            return f"sold out on the night of {_said(night)}"
    return None


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _find(state: dict, reference: str) -> dict:
    wanted = _digits(reference)
    for b in state["bookings"]:
        if wanted and _digits(b["reference"]) == wanted:
            return b
    raise ToolError(f"There is no booking {reference!r}. References look like LB-10421")


@tool("Which rooms are free for a stay, with the price of each night and the total, in LKR. Call it before saying "
      "any price or whether a room is free.",
      {"type": "object",
       "properties": {"check_in": _DATE, "check_out": _DATE,
                      "guests": {"type": "integer", "description": "How many people, children included"},
                      "room_type": {"type": "string", "enum": ROOM_TYPES,
                                    "description": "Leave out to list every room type"}},
       "required": ["check_in", "check_out", "guests"]})
def check_availability(state: dict, args: dict) -> dict[str, Any]:
    nights = _nights(state, args["check_in"], args["check_out"])
    rooms = []
    for room_type in ([args["room_type"]] if args.get("room_type") else ROOM_TYPES):
        room = state["rooms"][room_type]
        entry: dict[str, Any] = {"room_type": room_type, "name": room["name"], "max_guests": room["max_guests"]}
        reason = _why_not(state, room_type, nights, args["guests"])
        if reason:
            entry |= {"available": False, "reason": reason}
        else:
            prices = [_price(state, room_type, n) for n in nights]
            entry |= {"available": True, "nightly_rates": prices, "total": sum(prices)}
        rooms.append(entry)
    return {"check_in": nights[0].isoformat(), "check_out": args["check_out"], "nights": len(nights),
            "guests": args["guests"], "currency": "LKR", "breakfast_included": True, "rooms": rooms}


@tool("Book a room. Only after the caller agreed to the room, the dates, the guests, the name and the phone number "
      "you read back to them.",
      {"type": "object",
       "properties": {"guest_name": {"type": "string"}, "phone": {"type": "string"},
                      "room_type": {"type": "string", "enum": ROOM_TYPES},
                      "check_in": _DATE, "check_out": _DATE, "guests": {"type": "integer"}},
       "required": ["guest_name", "phone", "room_type", "check_in", "check_out", "guests"]})
def create_booking(state: dict, args: dict) -> dict[str, Any]:
    nights = _nights(state, args["check_in"], args["check_out"])
    reason = _why_not(state, args["room_type"], nights, args["guests"])
    if reason:
        raise ToolError(f"Can't book: {reason}")
    reference = f"LB-{state['next_reference']}"
    state["next_reference"] += 1
    booking = {"reference": reference, "guest_name": args["guest_name"].strip(), "phone": _digits(args["phone"]),
               "room_type": args["room_type"], "check_in": nights[0].isoformat(), "check_out": args["check_out"],
               "guests": args["guests"], "status": "confirmed",
               "total": sum(_price(state, args["room_type"], n) for n in nights)}
    state["bookings"].append(booking)
    return {**booking, "currency": "LKR", "room_name": state["rooms"][args["room_type"]]["name"]}


@tool("Find bookings by reference, by the guest's name, or by phone number. Each confirmed booking says "
      "what cancelling it now would cost (cancellation_fee_now, LKR).",
      {"type": "object",
       "properties": {"reference": {"type": "string", "description": "Such as LB-10421"},
                      "guest_name": {"type": "string"}, "phone": {"type": "string"}}})
def find_booking(state: dict, args: dict) -> dict[str, Any]:
    if not any(args.get(k) for k in ("reference", "guest_name", "phone")):
        raise ToolError("Give a reference, the guest's name or a phone number")
    found = []
    for b in state["bookings"]:
        if args.get("reference") and _digits(args["reference"]) and \
                _digits(args["reference"]) == _digits(b["reference"]):
            found.append(b)
        elif args.get("guest_name") and set(args["guest_name"].lower().split()) <= set(b["guest_name"].lower().split()):
            found.append(b)
        elif args.get("phone") and len(_digits(args["phone"])) >= 9 and \
                _digits(args["phone"])[-9:] == b["phone"][-9:]:
            found.append(b)
    return {"found": bool(found), "bookings": [_with_fee(state, b) for b in found]}


def _fee_now(state: dict, booking: dict) -> int:
    """Free until 48 hours before check-in; after that, the first night."""
    check_in = date.fromisoformat(booking["check_in"])
    late = (check_in - date.fromisoformat(state["today"])).days < 2
    return _price(state, booking["room_type"], check_in) if late else 0


def _with_fee(state: dict, booking: dict) -> dict:
    found = dict(booking)
    if booking["status"] == "confirmed":
        found["cancellation_fee_now"] = _fee_now(state, booking)
    return found


@tool("Cancel a booking by its reference. Free until 48 hours before check-in; after that the first night is "
      "charged. Quote the fee from find_booking and get a yes before you cancel.",
      {"type": "object", "properties": {"reference": {"type": "string"}}, "required": ["reference"]})
def cancel_booking(state: dict, args: dict) -> dict[str, Any]:
    booking = _find(state, args["reference"])
    if booking["status"] == "cancelled":
        raise ToolError(f"{booking['reference']} is already cancelled")
    fee = _fee_now(state, booking)
    booking["status"] = "cancelled"
    booking["cancellation_fee"] = fee
    return {"reference": booking["reference"], "status": "cancelled", "cancellation_fee": fee, "currency": "LKR"}


@tool("The hotel's own answer on one topic. Use it for any question about the hotel.",
      {"type": "object",
       "properties": {"topic": {"type": "string", "enum": ["check_in", "check_out", "breakfast", "airport", "parking",
                                                           "pool", "cancellation", "children", "pets", "wifi"]}},
       "required": ["topic"]})
def get_hotel_info(state: dict, args: dict) -> dict[str, Any]:
    answer = (state.get("info") or {}).get(args["topic"])
    if not answer:
        raise ToolError(f"No information on {args['topic']}")
    return {"topic": args["topic"], "answer": answer}


@tool("Hand the call to the duty manager: for complaints, billing problems, or a caller who asks for a person.",
      {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]})
def transfer_to_human(state: dict, args: dict) -> dict[str, Any]:
    state["transferred"] = args["reason"]
    return {"transferred": True, "note": "The duty manager takes the call now. Tell the caller, in one sentence."}
