"""
Static frontend checks that need no browser: broken local asset references,
unsafe DOM APIs, and a few basic accessibility/SEO facts. These ran for real
in the audit sandbox (plain file reads + regex/HTML parsing, no network or
fastapi/httpx needed) -- see the audit report for exact results. They are
included here as a permanent, repeatable regression check alongside the
API/security tests, using pytest for a single consistent test entrypoint.
"""
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "app" / "static"
INDEX_HTML = (STATIC_DIR / "index.html").read_text()
APP_JS = (STATIC_DIR / "assets" / "app.js").read_text()


def test_no_innerhtml_or_outerhtml_in_frontend_js():
    """User-controlled content must never be assigned through innerHTML/
    outerHTML/insertAdjacentHTML; textContent and safe DOM APIs only."""
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert unsafe not in APP_JS, f"found unsafe DOM API usage: {unsafe}"


def test_no_inline_event_handlers_in_html():
    assert not re.search(r'\son[a-z]+\s*=\s*"', INDEX_HTML, re.IGNORECASE)


def test_no_javascript_or_data_uri_hrefs():
    for match in re.findall(r'href="([^"]*)"', INDEX_HTML):
        assert not match.lower().startswith("javascript:")


def test_all_local_asset_references_exist_on_disk():
    local_refs = re.findall(r'(?:href|src)="(/assets/[^"]+)"', INDEX_HTML)
    assert local_refs, "expected at least one /assets/ reference in index.html"
    for ref in local_refs:
        asset_path = STATIC_DIR / ref.lstrip("/")
        assert asset_path.is_file(), f"referenced asset does not exist: {ref}"


def test_all_internal_anchor_targets_exist_in_page():
    """Every in-page anchor link (#section) must match a real id in the
    document."""
    ids_in_page = set(re.findall(r'\sid="([^"]+)"', INDEX_HTML))
    anchors = re.findall(r'href="#([^"]+)"', INDEX_HTML)
    for anchor in anchors:
        assert anchor in ids_in_page, f"dangling in-page anchor: #{anchor}"


def test_external_links_use_https():
    for match in re.findall(r'href="(https?://[^"]+)"', INDEX_HTML):
        assert match.startswith("https://"), f"external link not using HTTPS: {match}"


def test_external_links_have_noopener():
    """Every target=_blank link should carry rel=noopener (noreferrer is a
    bonus) to avoid the window.opener reverse-tabnabbing issue."""
    blank_links = re.findall(r'<a\s+[^>]*target="_blank"[^>]*>', INDEX_HTML)
    assert blank_links, "expected at least one target=_blank social link"
    for tag in blank_links:
        assert "noopener" in tag, f"target=_blank link missing rel=noopener: {tag}"


def test_canonical_and_viewport_present():
    assert '<link rel="canonical"' in INDEX_HTML
    assert 'name="viewport"' in INDEX_HTML


def test_single_h1_on_page():
    """Exactly one <h1> keeps the heading hierarchy sane for a one-page
    site."""
    assert len(re.findall(r"<h1[\s>]", INDEX_HTML)) == 1


def test_decorative_hero_scene_is_aria_hidden():
    assert 'class="hero-scene reveal" aria-hidden="true"' in INDEX_HTML


def test_form_has_accessible_label():
    assert 'for="anonymousMessage"' in INDEX_HTML
    assert 'id="anonymousMessage"' in INDEX_HTML


@pytest.mark.parametrize("required_file", ["robots.txt", "sitemap.xml"])
def test_seo_files_present(required_file):
    assert (STATIC_DIR / required_file).is_file()
