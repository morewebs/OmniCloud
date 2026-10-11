"""The public site is built from the repo's own markdown.

`site/build.py` is the seam: given a repo root it writes a static site, and
the only thing these tests observe is that output. Expected link targets are
literals from the published documents, not recomputed by the builder.

This file never imports the app, so it must not pull in the suite's conftest
(that one needs the panel's dependencies, and the site build deliberately
does not).
"""
pytest_plugins: list[str] = []
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "site" / "build.py"


def _build(tmp_path: Path) -> Path:
    out = tmp_path / "dist"
    proc = subprocess.run(
        [sys.executable, str(BUILD), "--root", str(REPO), "--out", str(out)],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return out


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("site"))


def test_build_emits_the_operator_pages_and_nothing_internal(site):
    pages = sorted(p.relative_to(site).as_posix()
                   for p in site.rglob("*.html"))
    assert pages == [
        "404.html",
        "changelog/index.html",
        "contributing/index.html",
        "deploy/index.html",
        "guide/index.html",
        "index.html",
        "install/index.html",
        "ip-change/index.html",
        "mcp/index.html",
        "security/index.html",
    ]
    for page in site.rglob("*.html"):
        for href in re.findall(r'href="([^"]*)"', page.read_text(encoding="utf-8")):
            assert "provider-truth" not in href, page.name
            assert not href.endswith("design.md"), page.name


def test_guide_keeps_the_readme_and_points_its_links_at_the_site(site):
    guide = (site / "guide" / "index.html").read_text(encoding="utf-8")
    assert "An open-source, multi-provider cloud control panel." in guide
    assert "img.shields.io" not in guide
    assert "badge.svg" not in guide
    assert 'href="../ip-change/"' in guide
    assert 'href="../mcp/"' in guide
    assert 'href="../deploy/"' in guide
    assert 'href="../contributing/"' in guide
    # The link target moved; the visible text stays the repo's own words.
    assert 'href="../ip-change/">docs/ip-change.md</a>' in guide
    assert 'href="../mcp/">docs/mcp.md</a>' in guide


def test_cross_document_anchors_survive_the_rewrite(site):
    mcp = (site / "mcp" / "index.html").read_text(encoding="utf-8")
    # docs/mcp.md links to the reverse-proxy section of DEPLOY.md
    assert 'href="../deploy/#reverse-proxy"' in mcp
    security = (site / "security" / "index.html").read_text(encoding="utf-8")
    assert 'href="../deploy/"' in security


def test_unpublished_files_are_named_but_not_linked(site):
    # The contributing guide cites the two files the site refuses to publish.
    # They stay as the repo wrote them — names, not links — so a reader of the
    # site is not sent to either document.
    contributing = (site / "contributing" / "index.html").read_text(encoding="utf-8")
    assert "docs/provider-truth.md" in contributing
    assert "design.md" in contributing
    for href in re.findall(r'href="([^"]*)"', contributing):
        assert "provider-truth" not in href
        assert "design.md" not in href
    mcp = (site / "mcp" / "index.html").read_text(encoding="utf-8")
    assert 'href="https://modelcontextprotocol.io"' in mcp


def test_pages_are_self_contained_static_html(site):
    for page in site.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "<script" not in html.lower(), page.name
        assert 'rel="stylesheet"' in html
    assert (site / "assets" / "fonts" / "geist-latin.woff2").is_file()
    assert (site / "assets" / "fonts" / "geist-mono-latin.woff2").is_file()
    home = (site / "index.html").read_text(encoding="utf-8")
    assert 'href="install/"' in home
    assert 'href="guide/"' in home
    assert "srv-fsn1-01" in home
    install = (site / "install" / "index.html").read_text(encoding="utf-8")
    assert "uv run python demo_server.py" in install
    assert "docker run -d -p 8000:8000" in install
    assert 'href="../deploy/"' in install
