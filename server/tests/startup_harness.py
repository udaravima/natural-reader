"""Run startup.sh functions in a throwaway copy of the project.

startup.sh cd's to its own directory when sourced, so tests source a COPY in
tmp_path: nothing touches the real .env, data/ or containers. Binaries the
script calls (podman, podman-compose, curl, npm, loginctl, systemctl, …) are
replaced by stubs that append "name args…" to a calls file.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROJECT_FILES = ["startup.sh", "docker-compose.yml", "compose.podman.yml", "compose.dev.yml",
                 "compose.profile.yml", "searxng/settings.yml.example", "package.json"]


def make_project(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    for f in PROJECT_FILES:
        src = REPO / f
        if src.exists():
            (proj / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, proj / f)
    return proj


def stub_bin(bindir: Path, names, calls: Path, codes: dict | None = None) -> Path:
    bindir.mkdir(parents=True, exist_ok=True)
    for name in names:
        code = (codes or {}).get(name, 0)
        p = bindir / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nexit {code}\n')
        p.chmod(0o755)
    return bindir


def tools_dir(bindir: Path, names) -> Path:
    """A PATH dir holding only symlinks to the named real tools."""
    bindir.mkdir(parents=True, exist_ok=True)
    for n in names:
        real = shutil.which(n)
        assert real, f"{n} not found on this machine"
        (bindir / n).symlink_to(real)
    return bindir


def run(project: Path, script: str, path: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base = {"HOME": str(project), "PATH": path, "USER": os.environ.get("USER", "tester")}
    base.update(env or {})
    return subprocess.run(["bash", "-c", f"source ./startup.sh; {script}"], cwd=project,
                          env=base, capture_output=True, text=True, timeout=120)


SYSTEM_PATH = "/usr/bin:/bin"
