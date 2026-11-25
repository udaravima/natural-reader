"""Phone knobs (spec §5.9). Every value is an env var with a safe default; a bad number logs a WARNING and falls
back, as `load_chat_config` does. The AudioSocket listener has no authentication, so it is off unless
`PHONE_AUDIOSOCKET_LISTEN` is set, and a listener that is not on loopback is also off unless
`PHONE_AUDIOSOCKET_ALLOW` names the peers that may connect."""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Callable, Mapping

from ..services.inference_limits import seconds as duration_seconds

logger = logging.getLogger(__name__)

WILDCARD_HOSTS = ("0.0.0.0", "::")


@dataclass(frozen=True)
class PhoneConfig:
    listen: str | None                 # "host:port" to listen on; None = the listener is off
    allow: tuple[str, ...]             # peer IPs allowed to connect (normalised; IPv4-mapped IPv6 is plain IPv4)
    pbx_address: str | None            # "host:port" the PBX dials (the console's snippet); None = unknown
    max_calls: int = 2                 # calls at once across all lines
    idle_s: float = 30                 # seconds of caller silence before "Are you still there?", and again before hang-up
    # seconds of unbroken caller sound heard as one clip; longer talk (or a TV, or hold music) is cut into clips this
    # long (R5.8). 30 s is Whisper's own window, and it bounds a call's hearing memory to about 2 MB.
    max_utterance_s: float = 30
    # whole seconds Ollama keeps a line's model loaded after a load request or a turn (R17.1), within
    # INFERENCE_KEEP_ALIVE_MAX; 0 = off (the phone sends no load request and no keep_alive); -1 = Ollama's "forever"
    model_keep_alive_s: int = 900


def normalize_ip(text: str) -> str | None:
    """The canonical text of an IP address, or None when `text` isn't one. An IPv4-mapped IPv6 address
    (`::ffff:192.0.2.9`, how a dual-stack socket shows an IPv4 peer) becomes its IPv4 form."""
    try:
        ip = ipaddress.ip_address(text.strip().split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return str(ip)


def is_loopback_host(host: str) -> bool:
    """True for `localhost` and loopback addresses. A wildcard or any other name is not loopback: a name could
    resolve anywhere."""
    host = host.strip().lower()
    if host == "localhost":
        return True
    ip = normalize_ip(host)
    return ip is not None and ipaddress.ip_address(ip).is_loopback


def split_listen(listen: str) -> tuple[str, int] | None:
    """`host:port` (or `[v6]:port`) as (host, port); None when it isn't one. Port 0 is allowed (pick a free one)."""
    host, sep, port = listen.strip().rpartition(":")
    host = host.removeprefix("[").removesuffix("]")
    if not sep or not host or not port.isdigit() or int(port) > 65535:
        return None
    return host, int(port)


def load_phone_config(env: Mapping[str, str]) -> PhoneConfig:
    d = PhoneConfig(listen=None, allow=(), pbx_address=None)

    def num(key: str, default, cast: Callable, lo, hi):
        raw = (env.get(key) or "").strip()
        if not raw:
            return default
        try:
            value = cast(raw)
        except ValueError:
            logger.warning("%s=%r is not a number; using %s", key, raw, default)
            return default
        if not lo <= value <= hi:
            logger.warning("%s=%r is outside %s..%s; using %s", key, raw, lo, hi, default)
            return default
        return value

    def duration(key: str, default: int) -> int:
        """A duration read as the app reads its other keep-alive values (`inference_limits.seconds`: "15m", "1h",
        "900"), in whole seconds; any negative value is Ollama's "forever", -1."""
        raw = (env.get(key) or "").strip()
        if not raw:
            return default
        value = duration_seconds(raw)
        if value is None:
            logger.warning("%s=%r is not a duration (like 15m or 900); using %ds", key, raw, default)
            return default
        return -1 if value < 0 else int(round(value))

    allow: list[str] = []
    for item in (env.get("PHONE_AUDIOSOCKET_ALLOW") or "").split(","):
        if not item.strip():
            continue
        ip = normalize_ip(item)
        if ip is None:
            logger.warning("PHONE_AUDIOSOCKET_ALLOW: %r is not an IP address; skipped", item.strip())
        elif ip not in allow:
            allow.append(ip)

    listen: str | None = None
    raw_listen = (env.get("PHONE_AUDIOSOCKET_LISTEN") or "").strip()
    if raw_listen:
        parts = split_listen(raw_listen)
        if parts is None:
            logger.warning("PHONE_AUDIOSOCKET_LISTEN=%r is not host:port; the phone listener is off", raw_listen)
        elif not allow and not is_loopback_host(parts[0]):
            logger.warning("PHONE_AUDIOSOCKET_LISTEN=%r is not a loopback address and PHONE_AUDIOSOCKET_ALLOW is "
                           "empty; the phone listener is off. List the peers that may connect.", raw_listen)
        else:
            listen = raw_listen

    pbx = (env.get("PHONE_PBX_ADDRESS") or "").strip() or None
    if pbx is None and listen is not None:
        host = split_listen(listen)[0]
        if host not in WILDCARD_HOSTS:           # a wildcard can't be dialled
            pbx = listen

    return PhoneConfig(
        listen=listen,
        allow=tuple(allow),
        pbx_address=pbx,
        max_calls=num("PHONE_MAX_CALLS", d.max_calls, int, 1, 20),
        idle_s=num("PHONE_IDLE_S", d.idle_s, float, 1.0, 600.0),
        max_utterance_s=num("PHONE_MAX_UTTERANCE_S", d.max_utterance_s, float, 5.0, 120.0),
        model_keep_alive_s=duration("PHONE_MODEL_KEEP_ALIVE", d.model_keep_alive_s),
    )
