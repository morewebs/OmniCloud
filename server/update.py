"""Built-in updates from GitHub.

Check: queries the GitHub releases API for the repo's latest release and
compares against server.version.VERSION. Runs daily off the catalog loop's
wake cadence (no separate task) - failures are silent, a check is best-effort.

Apply: git pull + dependency sync + SPA build, then the process exits with a
documented code so the supervisor (Docker restart policy / systemd / loop
script - see DEPLOY.md) restarts it on the new code. A process cannot
cleanly replace itself while running; the exit IS the mechanism.
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path

import httpx

from . import version

log = logging.getLogger("omnicloud.update")

REPO = os.environ.get("OMNICLOUD_UPDATE_REPO", "morewebs/OmniCloud")
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
# the supervisor restarts the process after an apply; this exit code is the
# handshake (documented in DEPLOY.md)
EXIT_UPDATE = 78

REPO_ROOT = Path(__file__).resolve().parent.parent
_status: dict = {"checked_at": None, "latest": None, "notes": None,
                "url": None, "error": None, "applying": False}


def status() -> dict:
    return {**_status, "current": version.VERSION, "repo": REPO}


async def check() -> dict:
    """One best-effort GitHub check; stores the result for the UI."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(API_URL, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            rel = r.json()
        _status.update(checked_at=_now(), latest=rel.get("tag_name", "").lstrip("v"),
                       notes=(rel.get("body") or "")[:2000],
                       url=rel.get("html_url"), error=None)
    except Exception as e:  # noqa: BLE001 - a failed check is not an incident
        _status.update(checked_at=_now(), error=f"{type(e).__name__}: {e}"[:300])
        log.info("update check failed: %s", _status["error"])
    return status()


def is_newer(latest: str, current: str) -> bool:
    def tup(v: str):
        return tuple(int(p) for p in v.split(".") if p.isdigit())
    try:
        return tup(latest) > tup(current)
    except (ValueError, TypeError):
        return False


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def apply() -> dict:
    """One-click update: pull, sync deps, build the SPA, then exit so the
    supervisor restarts on the new code. Returns BEFORE exiting only on
    pre-flight failure; a successful apply ends the process."""
    if _status["applying"]:
        raise RuntimeError("an update is already in progress")
    if not REPO_ROOT.is_dir() or subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=REPO_ROOT,
            capture_output=True).returncode != 0:
        raise RuntimeError("this install is not a git checkout - "
                           "update manually (see DEPLOY.md)")
    # a dirty tree would conflict with the pull
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        raise RuntimeError("working tree has local changes - commit or stash "
                           f"them first. ({len(dirty.splitlines())} changed paths)")
    if not (REPO_ROOT / "web" / "package.json").exists():
        raise RuntimeError("web/ missing - cannot rebuild the SPA")
    _status["applying"] = True
    log.info("applying update to %s", _status.get("latest"))
    # run the steps out-of-band; the final exit can't be awaited
    asyncio.get_running_loop().run_in_executor(
        None, _apply_sequence)
    return {"ok": True, "detail": "update running - the panel restarts when done"}


def _apply_sequence() -> None:
    """Pull -> deps -> SPA build, then exit for the supervisor restart."""
    import time
    for label, cmd in [
        ("git pull", ["git", "pull", "--ff-only"]),
        ("uv sync", ["uv", "sync", "--frozen", "--no-dev"]),
        ("npm install", ["npm", "install", "--no-audit", "--no-fund"]),
        ("npm run build", ["npm", "run", "build"]),
    ]:
        cwd = REPO_ROOT / "web" if cmd[0] == "npm" else REPO_ROOT
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=1800)
        if r.returncode != 0:
            log.error("update step '%s' failed: %s", label, r.stderr[-800:])
            _status.update(applying=False,
                           error=f"'{label}' failed - see server logs")
            return
        log.info("update step ok: %s", label)
    log.info("update applied - exiting for supervisor restart")
    # give the response a beat to flush, then exit; the supervisor restarts
    time.sleep(1)
    os._exit(EXIT_UPDATE)
