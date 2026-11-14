"""A rebuild installs the versions the tests ran on: every requirement has a ceiling, and the image installs
through constraints.txt, the exact versions of everything requirements.txt (and requirements-dev.txt, the
test harness) brings in. The image installs requirements.txt only: no test package ships."""
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


LISTS = ("requirements.txt", "requirements-dev.txt")
TEST_PACKAGES = {"pytest", "pytest-asyncio", "respx", "python-dotenv"}


def test_every_requirement_has_a_floor_and_a_ceiling():
    loose = [str(r) for name in LISTS for r in _requirements(name)
             if canonicalize_name(r.name) not in NO_CEILING
             and not ({s.operator for s in r.specifier} >= {">=", "<"})]
    assert loose == []


def test_the_constraints_pin_every_requirement_inside_its_range():
    pins = _pins()
    for req in (r for name in LISTS for r in _requirements(name)):
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


def test_the_test_harness_is_not_in_the_list_the_image_installs():
    runtime = {canonicalize_name(r.name) for r in _requirements("requirements.txt")}
    dev = {canonicalize_name(r.name) for r in _requirements("requirements-dev.txt")}
    assert runtime & TEST_PACKAGES == set()
    assert dev >= TEST_PACKAGES
    text = (ROOT / "Containerfile").read_text()
    assert "requirements-dev" not in text and "pytest" not in text
    assert "!requirements-dev.txt" not in (ROOT / ".dockerignore").read_text()
