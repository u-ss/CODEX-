# -*- coding: utf-8 -*-

from . import __init__  # noqa: F401

from ..locator import build_locator, is_strong_locator, parse_locator


def test_build_locator_exact_match_is_strong():
    content = "Hello world.\n\nSecond paragraph."
    quote = "Hello world."
    loc = build_locator(url="https://example.com", content=content, quote=quote)
    assert loc
    assert is_strong_locator(loc)
    obj = parse_locator(loc)
    assert obj and obj["url"] == "https://example.com"


def test_build_locator_html_has_heading_and_paragraph_index():
    content = "<h2>Section</h2><p>Alpha beta gamma.</p><p>Delta epsilon.</p>"
    quote = "Delta epsilon."
    loc = build_locator(url="https://example.com", content=content, quote=quote)
    assert loc and is_strong_locator(loc)
    obj = parse_locator(loc)
    assert obj and obj["heading"] == "Section"
    assert obj["paragraph_index"] == 1
