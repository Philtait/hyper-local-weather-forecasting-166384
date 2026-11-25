"""Build the website's HTML pages from site_src/ and the strings in web/i18n/en.json.

Usage: python scripts/build_pages.py [--check]
Every page shares one header, footer and bottom navigation (site_src/layout.html);
the page bodies live in site_src/pages/. The output is plain HTML in web/, so the
site itself still has no build step. --check exits with 1 if a page is out of date.

Placeholders: {{t:section.key}} for a string, {{icon:name}} for an icon from
web/img/icons.svg.
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site_src"
WEB = ROOT / "web"
STRINGS = WEB / "i18n" / "en.json"

# key, output file, title string, nav icon, extra head lines, scripts
PAGES = [
    ("home", "index.html", "home.title", "home",
     ['<link rel="preload" href="img/about-field.webp" as="image" type="image/webp">'], ["js/map-logic.js", "js/home.js"]),
    ("how", "how-it-works.html", "how.page_title", "info", [], []),
    ("map", "map.html", "map.page_title", "pin",
     ['<link rel="stylesheet" href="vendor/leaflet/leaflet.css">'], ["vendor/leaflet/leaflet.js", "js/units.js", "js/map-logic.js", "js/map.js"]),
    ("sms", "sms.html", "sms.page_title", "message", [], ["js/units.js", "js/sms-message.js", "js/sms.js"]),
    ("contact", "contact.html", "contact.page_title", "mail", [], ["js/contact.js"]),
]
# Pages that are not in the main navigation: key, output file, title string, nav item kept highlighted, head, scripts
EXTRA_PAGES = [
    ("station", "station.html", "station.page_title", "map", [],
     ["js/units.js", "js/map-logic.js", "js/station-logic.js", "js/charts.js", "js/station.js"]),
]
PLACEHOLDER = re.compile(r"\{\{(t|icon):([\w.]+)\}\}")


def lookup(strings, path):
    node = strings
    for part in path.split("."):
        node = node[part]
    if not isinstance(node, str):
        raise KeyError(path)
    return node


def icon(name):
    return (f'<svg class="icon" aria-hidden="true" focusable="false">'
            f'<use href="img/icons.svg#{name}"></use></svg>')


def fill(text, strings):
    def replace(match):
        kind, key = match.groups()
        return html.escape(lookup(strings, key)) if kind == "t" else icon(key)
    return PLACEHOLDER.sub(replace, text)


def nav_items(current, strings, bottom):
    items = []
    for key, filename, _, nav_icon, _, _ in PAGES:
        here = ' aria-current="page"' if key == current else ""
        label = html.escape(strings["nav"][key])
        inner = f"{icon(nav_icon)}<span>{label}</span>" if bottom else label
        items.append(f'    <li><a href="{filename}"{here}>{inner}</a></li>')
    return "\n".join(items)


def render(page, strings, nav_current=None):
    key, _, title, _, head, scripts = page
    layout = (SRC / "layout.html").read_text(encoding="utf-8")
    body = (SRC / "pages" / f"{key}.html").read_text(encoding="utf-8").strip()
    parts = {
        "lang": strings["lang"], "page": key, "page_title": html.escape(lookup(strings, title)),
        "head": "".join(line + "\n" for line in head), "body": body,
        "nav": nav_items(nav_current or key, strings, False), "bottom_nav": nav_items(nav_current or key, strings, True),
        "scripts": "".join(f'<script src="{src}"></script>\n' for src in scripts),
    }
    text = re.sub(r"\{\{(\w+)\}\}", lambda m: parts[m.group(1)], layout)
    return fill(text, strings)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report pages that are out of date")
    args = parser.parse_args(argv)
    strings = json.loads(STRINGS.read_text(encoding="utf-8"))
    stale = []
    for page, nav_current in [(page, None) for page in PAGES] + [(page, page[3]) for page in EXTRA_PAGES]:
        target = WEB / page[1]
        text = render(page, strings, nav_current)
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != text:
                stale.append(page[1])
        else:
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"Wrote {target.relative_to(ROOT)}")
    if stale:
        print("Out of date: " + ", ".join(stale) + ". Run python scripts/build_pages.py")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
