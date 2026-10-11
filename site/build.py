"""Build the OmniCloud GitHub Pages site.

Reads the repo's own markdown and the two authored pages, rewrites links, and
writes static HTML. Nothing here is served at runtime: GitHub Actions installs
site/requirements.txt, runs this, and uploads site/dist. The renderer is not a
panel dependency.

    python site/build.py [--root .] [--out site/dist]
"""
import argparse
import html
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin

HERE = Path(__file__).resolve().parent

REPO_SLUG = "morewebs/OmniCloud"
GITHUB = f"https://github.com/{REPO_SLUG}"

# source file -> site directory, one level under the site root.
PAGES = {
    "README.md": "guide",
    "DEPLOY.md": "deploy",
    "SECURITY.md": "security",
    "CONTRIBUTING.md": "contributing",
    "CHANGELOG.md": "changelog",
    "docs/ip-change.md": "ip-change",
    "docs/mcp.md": "mcp",
}

NAV = [
    ("install/", "Install"),
    ("guide/", "Guide"),
    ("deploy/", "Deploy"),
    ("ip-change/", "IP change"),
    ("mcp/", "MCP"),
    ("changelog/", "Changelog"),
]

# Repo-relative files the site may link to but must not publish.
GITHUB_FILES = {
    "design.md": f"{GITHUB}/blob/main/design.md",
    "docs/provider-truth.md": f"{GITHUB}/blob/main/docs/provider-truth.md",
    "LICENSE": f"{GITHUB}/blob/main/LICENSE",
}


def renderer() -> MarkdownIt:
    return (MarkdownIt("commonmark", {"html": False, "linkify": False})
            .enable(["table", "strikethrough"])
            .use(anchors_plugin, permalink=False))


def _repo_relative(source: str, link_path: str) -> str | None:
    parts = list(Path(source).parent.parts)
    for part in Path(link_path).parts:
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part not in ("", "."):
            parts.append(part)
    return "/".join(parts)


def rewrite_href(href: str, source: str) -> str:
    """Turn a markdown link into a site path, or a GitHub URL for files the
    site does not publish. Absolute and in-page links pass through."""
    if href.startswith(("#", "mailto:")):
        return href
    # urlsplit only separates a fragment when the link has a scheme, so a
    # relative "DEPLOY.md#reverse-proxy" arrives whole. Split it ourselves.
    path, _, fragment = href.partition("#")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc:
        return href
    # Resolve ".." against the source file's directory. A link that leaves the
    # repo is a mistake.
    target = _repo_relative(source, parts.path)
    if target is None:
        raise SystemExit(f"{source}: link escapes the repo: {href}")
    page = PAGES.get(target)
    if page:
        anchor = f"#{fragment}" if fragment else ""
        return f"../{page}/{anchor}"
    if target in GITHUB_FILES:
        url = GITHUB_FILES[target]
        if fragment:
            url += "#" + fragment
        return url
    if not parts.path:
        return href
    raise SystemExit(
        f"{source}: no site page for {href!r} — publish it, or add it to "
        f"GITHUB_FILES if it must stay off the site")


_HREF = re.compile(r'href="([^"]*)"')


def rewrite_html(body: str, source: str) -> str:
    def sub(match: re.Match) -> str:
        return f'href="{html.escape(rewrite_href(html.unescape(match.group(1)), source), quote=True)}"'
    return _HREF.sub(sub, body)


def page_html(title: str, body: str, depth: int, current: str) -> str:
    prefix = "../" * depth
    def item(href: str, label: str) -> str:
        current_attr = ' aria-current="page"' if href.rstrip("/") == current else ""
        return f'        <a href="{prefix}{href}"{current_attr}>{label}</a>'

    nav = "\n".join(item(href, label) for href, label in NAV)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="OmniCloud is an open-source panel for server fleets, traffic, and billing across cloud providers.">
  <link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="{prefix}assets/site.css">
</head>
<body>
  <header class="bar">
    <a class="mark" href="{prefix or "./"}">OmniCloud</a>
    <nav>
{nav}
        <a href="{GITHUB}">GitHub</a>
    </nav>
  </header>
  <main>
{body}
  </main>
  <footer class="colophon">
    <p>OmniCloud {html.escape(_version())} · MIT · <a href="{prefix}security/">Security</a> · <a href="{GITHUB}">Source</a></p>
  </footer>
</body>
</html>
"""


def _version() -> str:
    text = (HERE.parent / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("pyproject.toml has no version")
    return match.group(1)


_BADGE = re.compile(r"^\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)\s*$", re.MULTILINE)


def render_markdown(root: Path, source: str) -> tuple[str, str]:
    # Badge images are written for the GitHub README. On the site they load
    # from a third party and go stale, so the words survive and the image goes.
    text = _BADGE.sub("", (root / source).read_text(encoding="utf-8"))
    title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), "OmniCloud")
    body = renderer().render(text)
    return title, rewrite_html(body, source)


def build(root: Path, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "assets").mkdir()
    shutil.copy(HERE / "assets" / "site.css", out / "assets" / "site.css")
    shutil.copytree(HERE / "assets" / "fonts", out / "assets" / "fonts")
    shutil.copy(HERE / "assets" / "favicon.svg", out / "favicon.svg")

    home = (HERE / "pages" / "index.html").read_text(encoding="utf-8")
    (out / "index.html").write_text(
        page_html("OmniCloud", home, 0, ""), encoding="utf-8")
    install = (HERE / "pages" / "install.html").read_text(encoding="utf-8")
    (out / "install").mkdir()
    (out / "install" / "index.html").write_text(
        page_html("Install · OmniCloud", install, 1, "install"), encoding="utf-8")

    for source, directory in PAGES.items():
        title, body = render_markdown(root, source)
        dest = out / directory
        dest.mkdir()
        (dest / "index.html").write_text(
            page_html(f"{title} · OmniCloud", f'<article class="doc">\n{body}</article>',
                      1, directory),
            encoding="utf-8")

    missing = (HERE / "pages" / "404.html").read_text(encoding="utf-8")
    (out / "404.html").write_text(
        page_html("Page not found · OmniCloud", missing, 0, ""), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=HERE.parent)
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    args = parser.parse_args()
    build(args.root, args.out)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
