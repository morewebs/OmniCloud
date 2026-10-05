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
# Container installs that bind-mount a git checkout of the repo (DEPLOY.md
# "self-updating container") CAN use the one-click updater - apply runs
# git pull + uv sync + npm build inside the container and exits 78.
# OMNICLOUD_CONTAINER=1 without a checkout (code COPY'd into the image)
# can never git pull; those report "rebuild the image" and apply refuses.
CONTAINER_INSTALL = os.environ.get("OMNICLOUD_CONTAINER", "") == "1"
_status: dict = {"checked_at": None, "latest": None, "available": False,
                "notes": None, "url": None, "error": None, "applying": False}


def status() -> dict:
    st = {**_status, "current": version.VERSION, "repo": REPO}
    if CONTAINER_INSTALL and not _has_checkout():
        st["update_method"] = "rebuild-image"
    return st


def _has_checkout() -> bool:
    """A real git checkout REPO_ROOT can update in place (bind-mounted repo
    or a plain host install). COPY'd-into-image installs cannot."""
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                           cwd=REPO_ROOT, capture_output=True)
        return r.returncode == 0
    except FileNotFoundError:
        return False


async def check() -> dict:
    """One best-effort GitHub check; stores the result for the UI."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(API_URL, headers={"Accept": "application/vnd.github+json"})
            r.raise_for_status()
            rel = r.json()
        _status.update(checked_at=_now(), latest=rel.get("tag_name", "").lstrip("v"),
                       available=is_newer(rel.get("tag_name", "").lstrip("v"),
                                         version.VERSION),
                       notes=(rel.get("body") or "")[:2000],
                       url=rel.get("html_url"), error=None)
    except Exception as e:  # noqa: BLE001 - a failed check is not an incident
        _status.update(checked_at=_now(), latest=None, available=False,
                      error=f"{type(e).__name__}: {e}"[:300])
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
    if not _status["available"]:
        raise RuntimeError("no newer version is known - check for updates first")
    if CONTAINER_INSTALL and not _has_checkout():
        raise RuntimeError("container installs update by rebuilding the image - "
                           "git pull inside the container is impossible (see DEPLOY.md)")
    if not REPO_ROOT.is_dir():
        raise RuntimeError("this install is not a git checkout - "
                           "update manually (see DEPLOY.md)")
    try:
        inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                                cwd=REPO_ROOT, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("git is not installed - update manually (see DEPLOY.md)")
    if inside.returncode != 0:
        raise RuntimeError("this install is not a git checkout - "
                           "update manually (see DEPLOY.md)")
    # web/package-lock.json is machine-generated and safe to discard - a
    # previous apply's npm drift (or a host-side install) may have rewritten
    # it; restore BEFORE the dirty check or that exact scenario is refused
    # here (commit d289a3b made the same restore unreachable this way).
    subprocess.run(["git", "checkout", "--", "web/package-lock.json"],
                   cwd=REPO_ROOT, capture_output=True)
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
    # quiesce: stop the sync/catalog loops and drain in-flight work so the
    # exit can't land mid-transaction (same pattern as app shutdown). The
    # current task must not be drained - it is by definition pending while
    # awaiting, and asyncio.wait would burn the full timeout every time.
    from . import catalog, sync
    sync.stop_all()
    catalog.stop()
    pending = [t for t in asyncio.all_tasks()
               if t is not asyncio.current_task() and not t.done()]
    if pending:
        await asyncio.wait(pending, timeout=5)
    # run the steps out-of-band; the final exit can't be awaited
    asyncio.get_running_loop().run_in_executor(
        None, _apply_sequence)
    return {"ok": True, "detail": "update running - the panel restarts when done"}


def _apply_sequence() -> None:
    """Pull -> deps -> SPA build, then exit for the supervisor restart.

    Any failure (a failed step, or an unexpected exception like a missing
    git/uv/npm binary) must reset _status['applying'] and RESTART the
    sync/catalog loops that apply() stopped - otherwise the panel serves on
    with all loops dead and every later apply is refused with 'already in
    progress' until restart."""
    import time
    try:
        ok = _apply_steps()
    except Exception as e:  # noqa: BLE001 - the apply thread must never die silently
        log.exception("update apply crashed: %s", e)
        _status.update(applying=False,
                       error=f"{type(e).__name__}: {e}"[:300])
        _restart_loops()
        return
    if not ok:
        return  # the step already recorded its error + restarted the loops
    log.info("update applied - exiting for supervisor restart")
    # give the response a beat to flush, then exit; the supervisor restarts
    time.sleep(1)
    os._exit(EXIT_UPDATE)


def _apply_steps() -> bool:
    """Run the sequence; True = applied (caller exits), False = failed
    (error recorded, loops restarted)."""
    for label, cmd in [
        ("git pull", ["git", "pull", "--ff-only"]),
        ("uv sync", ["uv", "sync", "--frozen", "--no-dev"]),
        # npm ci, never npm install: install rewrites package-lock.json (npm
        # version drift), leaving the tree dirty so the NEXT apply's
        # --ff-only pull fails. ci installs exactly the lockfile and leaves
        # the tree clean.
        ("npm ci", ["npm", "ci", "--no-audit", "--no-fund"]),
        ("npm run build", ["npm", "run", "build"]),
    ]:
        cwd = REPO_ROOT / "web" if cmd[0] == "npm" else REPO_ROOT
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=1800)
        if r.returncode != 0:
            log.error("update step '%s' failed: %s", label, r.stderr[-800:])
            _status.update(applying=False,
                           error=f"'{label}' failed - see server logs")
            # the loops were stopped by apply()'s quiesce - bring them back
            # (the panel keeps serving, just without a restart on new code)
            _restart_loops()
            return False
        log.info("update step ok: %s", label)
    return True


def _restart_loops() -> None:
    """Re-spawn the sync/catalog loops the same way startup does. Runs in the
    executor thread: hop onto the app loop (start_all/start create tasks)."""
    from . import catalog, sync
    if sync._loop is not None and sync._loop.is_running():
        sync._loop.call_soon_threadsafe(lambda: (sync.start_all(), catalog.start()))
    else:
        log.error("cannot restart sync loops after failed apply - no app loop")
