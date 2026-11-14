"""The CI workflow runs the same gates, on the same versions, as a developer and the image do."""
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
TRIGGERS = WORKFLOW[True]      # YAML reads the key `on` as the boolean true


def _runs(job: str) -> str:
    return "\n".join(step.get("run", "") for step in WORKFLOW["jobs"][job]["steps"])


def test_it_runs_on_pull_requests_and_on_the_two_long_lived_branches():
    assert "pull_request" in TRIGGERS
    assert TRIGGERS["push"]["branches"] == ["master", "development"]
    assert WORKFLOW["permissions"] == {"contents": "read"}


def test_the_backend_job_tests_the_pinned_versions_on_a_scratch_postgres():
    job = WORKFLOW["jobs"]["backend"]
    assert "pip install -r requirements.txt -r requirements-dev.txt -c constraints.txt" in _runs("backend")
    assert "python -m pytest" in _runs("backend")
    assert job["services"]["postgres"]["image"] == "pgvector/pgvector:pg16"
    for name in ("TEST_DATABASE_URL", "TEST_ADMIN_DATABASE_URL"):
        assert ":5434/" in job["env"][name]          # never the app's own port: dbutil would refuse it
    python = next(s for s in job["steps"] if "setup-python" in s.get("uses", ""))
    containerfile = (ROOT / "Containerfile").read_text()
    assert f"python:{python['with']['python-version']}-" in containerfile      # the image's Python


def test_the_frontend_job_runs_lint_tests_and_build():
    runs = _runs("frontend")
    assert all(cmd in runs for cmd in ("npm ci", "npm run lint", "npx vitest run", "npm run build"))
