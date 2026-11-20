"""Build the static browser release with pygbag.

Stages only the modules the web build needs, runs pygbag with the vendored
template (network-independent apart from pygbag's engine download), injects
``web/shell.html`` into the generated index, and leaves a deployable folder.

    python tools/build_web.py            # -> build/lumenvalley/build/web
    python tools/build_web.py --serve    # ...then serve it on :8000

The staging folder exists because pygbag packages its app directory wholesale:
pointing it at the repository would ship tests, docs and reference images.
"""

import argparse
import http.server
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
APP = os.path.join(BUILD, "lumenvalley")

MODULES = (
    "entities.py", "levels.py", "game.py", "render.py", "background.py",
    "sprites.py", "sound.py", "music.py", "storage.py", "pygame_view.py",
)
PACKAGES = ("lumenvalley",)
ENTRY = ("web/main.py", "web_store.py")
EXTRA = ("web_store.py",)

TEMPLATE = os.path.join(ROOT, "web", "pygbag-default.tmpl")
SHELL = os.path.join(ROOT, "web", "shell.html")
# Regenerate with tools/make_icon.py. Passed to pygbag so the tab icon is the
# project's gem rather than the default favicon fetched from the CDN.
ICON = os.path.join(ROOT, "web", "favicon.png")
# Regenerate with tools/make_preview.py. Copied next to the page so a card can
# point at it; pygbag only ships the app and the icon.
PREVIEW = os.path.join(ROOT, "web", "preview.png")


def copy_tree_file(rel, dest_root):
    src = os.path.join(ROOT, rel)
    dest = os.path.join(dest_root, os.path.basename(rel))
    shutil.copyfile(src, dest)


def _clean(path, attempts=3):
    """Remove a previous build, tolerating Windows file locks briefly."""
    for attempt in range(attempts):
        if not os.path.isdir(path):
            return
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise SystemExit(
                    "error: cannot remove %s - stop the static server serving it"
                    % os.path.relpath(path, ROOT))
            time.sleep(1.5)


def stage():
    _clean(APP)
    os.makedirs(APP)
    for rel in MODULES:
        copy_tree_file(rel, APP)
    for pkg in PACKAGES:
        shutil.copytree(os.path.join(ROOT, pkg), os.path.join(APP, pkg),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # main.py must sit at the app root; web_store.py is its sibling import.
    shutil.copyfile(os.path.join(ROOT, "web", "main.py"),
                    os.path.join(APP, "main.py"))
    shutil.copyfile(os.path.join(ROOT, "web", "web_store.py"),
                    os.path.join(APP, "web_store.py"))
    shutil.copytree(os.path.join(ROOT, "assets"), os.path.join(APP, "assets"))
    print("staged", os.path.relpath(APP, ROOT))


def build(ume_block=1, base_url=None):
    if not os.path.isfile(ICON):
        print("error: %s is missing - run: python tools/make_icon.py"
              % os.path.relpath(ICON, ROOT))
        return 1
    if not os.path.isfile(PREVIEW):
        print("error: %s is missing - run: python tools/make_preview.py"
              % os.path.relpath(PREVIEW, ROOT))
        return 1
    cmd = [
        sys.executable, "-m", "pygbag", "--build",
        "--template", TEMPLATE,
        "--icon", ICON,
        "--title", "Lumenvalley",
        "--width", "1600", "--height", "480",
        "--package", "lumenvalley",
        "--ume_block", str(ume_block),
        APP,
    ]
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode
    out = os.path.join(APP, "build", "web")
    if not os.path.isdir(out):
        print("error: pygbag reported success but produced no build/web")
        return 1
    if not os.path.isfile(os.path.join(out, "favicon.png")):
        print("error: build has no favicon.png - pygbag ignored --icon")
        return 1
    shutil.copyfile(PREVIEW, os.path.join(out, "preview.png"))
    inject_shell(out)
    fix_icon_meta(out)
    fix_page_meta(out, base_url)
    check_runtime_scripts(out)
    print("\nweb build:", os.path.relpath(out, ROOT))
    print("size: %.1f MB" % (dir_size(out) / (1024.0 * 1024.0)))
    return 0


def inject_shell(out_dir):
    index = os.path.join(out_dir, "index.html")
    with open(SHELL, encoding="utf-8") as f:
        shell = f.read()
    with open(index, encoding="utf-8") as f:
        html = f.read()
    if "id=\"lv-shell\"" in html:
        print("shell: already present")
        return
    marker = "</body>"
    if marker not in html:
        raise SystemExit("error: generated index.html has no </body>")
    html = html.replace(marker, shell + "\n" + marker, 1)
    with open(index, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("shell: injected")


def png_size(path):
    """``(width, height)`` from a PNG IHDR, or None if it is not a PNG."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return (int.from_bytes(head[16:20], "big"),
            int.from_bytes(head[20:24], "big"))


def fix_icon_meta(out_dir):
    """Correct the vendored template's 16x16 favicon size hint.

    pygbag's template hardcodes ``sizes="16x16"``; the project icon is 64x64,
    and a hint that disagrees with the file is a sloppy claim the browser has to
    work around.  The template stays unmodified, the generated index is fixed.
    """
    size = png_size(os.path.join(out_dir, "favicon.png"))
    if size is None:
        print("warning: favicon.png is not a valid PNG; size hint left alone")
        return
    index = os.path.join(out_dir, "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    hint = 'sizes="%dx%d"' % size
    if 'sizes="16x16"' in html:
        html = html.replace('sizes="16x16"', hint, 1)
        with open(index, "w", encoding="utf-8", newline="\n") as f:
            f.write(html)
        print("favicon: %dx%d, size hint corrected" % size)
    elif hint in html:
        print("favicon: %dx%d" % size)
    else:
        print("warning: index.html has no favicon size hint to correct")


def fix_page_meta(out_dir, base_url=None):
    """Add the description and card metadata pygbag's template leaves out.

    ``og:image`` needs an absolute URL, so it is only written when the caller
    supplies ``--base-url`` (the Pages workflow passes the site's URL).  A
    relative image tag would be silently ignored by most consumers, which is
    worse than no tag: the build prints what it did and why.
    """
    index = os.path.join(out_dir, "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    if 'property="og:title"' in html:
        print("meta: card tags already present")
        return
    if "</title>" not in html:
        print("warning: index.html has no </title> to anchor the card metadata")
        return
    tags = [
        # The CPython/pygame runtime comes from the pygbag CDN; opening the TLS
        # connection while the shell paints saves a handshake from the critical
        # path of an 11 MB download.
        '<link rel="preconnect" href="https://pygame-web.github.io" crossorigin>',
        '<link rel="dns-prefetch" href="https://pygame-web.github.io">',
        '<meta name="description" content="Lumenvalley - a dash-and-jump '
        'platformer in the browser: six hand-built levels, keyboard or gamepad, '
        'no install.">',
        '<meta property="og:type" content="website">',
        '<meta property="og:title" content="Lumenvalley">',
        '<meta property="og:description" content="Dash, jump and chain gems '
        'through six hand-built levels.">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if base_url:
        tags.append('<meta property="og:image" content="%s/preview.png">'
                    % base_url.rstrip("/"))
    html = html.replace("</title>", "</title>\n" + "\n".join(tags), 1)
    with open(index, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)
    print("meta: description + card tags%s"
          % (" (absolute og:image)" if base_url else
             " (no og:image: pass --base-url once the site has a URL)"))


def check_runtime_scripts(out_dir):
    """Fail loudly if the page loads a runtime file the CDN no longer serves.

    pygbag's bootstrap fetches its virtual filesystem (``browserfs.min.js``)
    before it starts Python.  A 404 there is not a visible error: the browser
    blocks the opaque response (ERR_BLOCKED_BY_ORB) and startup waits forever,
    which is exactly the black page this build shipped once.  The vendored
    template pins the working copy; this guard catches a re-vendored or
    upgraded template that quietly goes back to a dead URL.
    """
    index = os.path.join(out_dir, "index.html")
    with open(index, encoding="utf-8") as f:
        html = f.read()
    if "browserfs.min.js" not in html:
        raise SystemExit(
            "error: the generated page never loads browserfs.min.js; pygbag's "
            "startup needs it and will hang without it")
    if "archives/0.9/browserfs.min.js" not in html:
        raise SystemExit(
            "error: the page points browserfs.min.js at a URL the CDN does "
            "not serve (cdn/0.9.3 answers 404, blocked as ERR_BLOCKED_BY_ORB, "
            "and the game never starts).  Pin "
            "https://pygame-web.github.io/archives/0.9/browserfs.min.js in "
            "web/pygbag-default.tmpl")


def dir_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            total += os.path.getsize(os.path.join(base, name))
    return total


def serve(directory, port):
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=directory, **kw)
    print("serving %s on http://localhost:%d/ (ctrl-c to stop)"
          % (os.path.relpath(directory, ROOT), port))
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--serve", action="store_true",
                    help="serve the build after creating it")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--ume-block", type=int, default=1, choices=(0, 1),
                    help="require a click before the engine starts "
                         "(0 is for automated headless checks only)")
    ap.add_argument("--base-url", default=None,
                    help="absolute site URL (for example "
                         "https://iams1am.github.io/LUMENVALLEY) so the "
                         "card's og:image can be written")
    args = ap.parse_args()
    stage()
    code = build(args.ume_block, args.base_url)
    if code == 0 and args.serve:
        serve(os.path.join(APP, "build", "web"), args.port)
    return code


if __name__ == "__main__":
    sys.exit(main())
