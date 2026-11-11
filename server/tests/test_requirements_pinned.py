"""A rebuild installs the versions the tests ran on: every requirement has a ceiling, and the image installs
through constraints.txt, the exact versions of everything requirements.txt brings in."""
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).parents[2]
NO_CEILING = {"tzdata"}   # the zone database: data, versioned by date, and newer is always what is wanted


def _requirements(name: str) -> list[Requirement]:
    lines = (line.split("#")[0].strip() for line in (ROOT / name).read_text().splitlines())
    return [Requirement(line) for line in lines if line]


def _pins() -> dict[str, str]:
    pins = {}
    for req in _requirements("constraints.txt"):
        (spec,) = req.specifier
        assert spec.operator == "==", f"{req} is not an exact pin"
        pins[canonicalize_name(req.name)] = spec.version
    return pins


def test_every_requirement_has_a_floor_and_a_ceiling():
    loose = [str(r) for r in _requirements("requirements.txt")
             if canonicalize_name(r.name) not in NO_CEILING
             and not ({s.operator for s in r.specifier} >= {">=", "<"})]
    assert loose == []


def test_the_constraints_pin_every_requirement_inside_its_range():
    pins = _pins()
    for req in _requirements("requirements.txt"):
        name = canonicalize_name(req.name)
        if name in NO_CEILING:
            continue
        assert name in pins, f"{name} is not pinned in constraints.txt"
        assert req.specifier.contains(pins[name]), f"constraints.txt pins {name}=={pins[name]}, outside {req}"


def test_the_versions_the_failing_uploads_came_from_are_excluded():
    # Starlette 1.0 answered a malformed upload with a 500; pgvector is used through its psycopg codec, whose
    # 0.x minors may change.
    by_name = {canonicalize_name(r.name): r for r in _requirements("requirements.txt")}
    assert not by_name["starlette"].specifier.contains("1.0.0")
    assert not by_name["pgvector"].specifier.contains("0.6.0")


def test_the_image_installs_through_the_constraints():
    text = (ROOT / "Containerfile").read_text()
    installs = [line for line in text.splitlines() if "pip install" in line and "-r requirements" in line]
    assert len(installs) == 2 and all("-c constraints.txt" in line for line in installs)
    assert "!constraints.txt" in (ROOT / ".dockerignore").read_text()
