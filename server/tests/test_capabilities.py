from server.auth.capabilities import caps_from_claims, KNOWN_CAPABILITIES


def test_caps_from_claims_intersects_known():
    claims = {"realm_access": {"roles": ["chat", "reader", "offline_access", "admin"]}}
    assert set(caps_from_claims(claims)) == {"chat", "reader", "admin"}


def test_caps_from_claims_empty_when_absent():
    assert caps_from_claims({}) == []
    assert caps_from_claims({"realm_access": {}}) == []


def test_known_capabilities_frozen():
    assert KNOWN_CAPABILITIES == frozenset({"reader", "chat", "admin"})
