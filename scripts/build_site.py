#!/usr/bin/env python3
"""
build_site.py
==============
Last step of the pipeline: stamps data/dashboard_data.json into the HTML
template and writes out a self-contained page -- no server, no build
tooling, no external files needed at runtime -- to TWO places, because
they have two different consumers with different requirements:

  * dist/value_board.html  -- a bare HTML fragment (no <!DOCTYPE>/<html>/
                               <head>/<body> of its own). This is the shape
                               the Claude Artifact tool wants -- it wraps
                               its own document skeleton around whatever
                               it's given, and asks for content WITHOUT
                               those tags. Open this locally in a browser
                               and it still renders fine (browsers are
                               lenient about a missing shell), but it's
                               not a properly-formed standalone document.

  * docs/index.html        -- the SAME content, but wrapped in a real,
                               complete HTML5 document (doctype, <html
                               lang>, a <head> with charset/viewport/title/
                               meta description). This is what GitHub
                               Pages is configured (see DEPLOY.md) to
                               actually serve to the public, and a proper
                               document shell matters there: it's what a
                               search engine reads to decide how to title
                               and describe the page in results, and it's
                               just correct HTML for something meant to
                               stand on its own at a public URL.

Why a template + injection step instead of just editing the HTML by hand:
the dashboard is one page with the data embedded directly in a <script>
tag (see site/value_board_template.html). That keeps it trivial to open
locally, but means every time the underlying data changes, the embedded
copy has to be regenerated -- that's all this script does.

USAGE
-----
    python3 build_site.py

(No arguments -- it always reads data/dashboard_data.json, which
build_dashboard_data.py is responsible for keeping up to date. After this
runs, `git add docs/index.html && git commit && git push` is what actually
updates the public site -- this script only writes the file locally.)
"""

import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_PATH = os.path.join(PROJECT_ROOT, "data", "dashboard_data.json")
TEMPLATE_PATH = os.path.join(PROJECT_ROOT, "site", "value_board_template.html")
DIST_PATH = os.path.join(PROJECT_ROOT, "dist", "value_board.html")
DOCS_PATH = os.path.join(PROJECT_ROOT, "docs", "index.html")

# This exact token appears once in the template, inside the page's
# <script> tag: `const RAW = __DASHBOARD_JSON__;`
INJECTION_TOKEN = "__DASHBOARD_JSON__"

# The template's own `<title>Value Board</title>` line is right for the
# Claude Artifact gallery (a short, specific name), but a public search
# result benefits from a more descriptive title and an explicit summary --
# neither of which a bare <title> tag alone provides. These are used only
# when building docs/index.html; dist/value_board.html keeps the template's
# original bare title as-is for the Artifact tool.
SEO_TITLE = "Value Board — Fantasy Football ADP Tracker"
SEO_DESCRIPTION = (
    "Value Board compares locked preseason fantasy football ADP against "
    "actual in-season performance, position by position, to surface who's "
    "outperforming or underperforming their draft slot."
)


def wrap_as_standalone_document(fragment: str) -> str:
    """Turn the bare template fragment into a complete, valid HTML5
    document -- for docs/index.html only (see module docstring)."""
    # The fragment's first line is its own `<title>...</title>`, meant for
    # the Artifact tool's wrapper. A standalone document needs the title
    # inside <head> instead, so drop that line here and supply SEO_TITLE
    # in its place below.
    body = re.sub(r"^<title>.*?</title>\s*\n", "", fragment, count=1)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{SEO_TITLE}</title>
<meta name="description" content="{SEO_DESCRIPTION}">
</head>
<body>
{body}</body>
</html>
"""


def main():
    with open(DATA_PATH) as f:
        dashboard_json_text = f.read().strip()  # already compact JSON, written by build_dashboard_data.py

    with open(TEMPLATE_PATH) as f:
        template = f.read()

    if INJECTION_TOKEN not in template:
        raise SystemExit(
            f"Couldn't find {INJECTION_TOKEN} in {TEMPLATE_PATH} -- has the template "
            "been edited in a way that removed it? The data injection point needs to "
            "stay intact for this script to work."
        )

    fragment = template.replace(INJECTION_TOKEN, dashboard_json_text)

    os.makedirs(os.path.dirname(DIST_PATH), exist_ok=True)
    with open(DIST_PATH, "w") as f:
        f.write(fragment)
    print(f"Wrote {DIST_PATH} ({len(fragment):,} bytes) -- Artifact-ready fragment")

    docs_page = wrap_as_standalone_document(fragment)
    os.makedirs(os.path.dirname(DOCS_PATH), exist_ok=True)
    with open(DOCS_PATH, "w") as f:
        f.write(docs_page)
    print(f"Wrote {DOCS_PATH} ({len(docs_page):,} bytes) -- standalone document for GitHub Pages")

    print(
        "\ndocs/index.html is what the public site (if deployed -- see DEPLOY.md) "
        "actually serves. Commit and push it to publish this update:\n"
        "  git add docs/index.html && git commit -m 'Refresh data' && git push"
    )


if __name__ == "__main__":
    main()
