"""Unit tests for security helpers + citations + search dedupe."""

from __future__ import annotations

import pytest

from app.core.security import validate_url, validate_filename, sniff_file_signature
from app.services.citations import (
    attach_sources_section,
    build_citations,
    dedupe_evidence,
    infer_quality,
    source_label,
    validate_markers,
)
from app.models.schemas import Evidence


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://localhost/x", "http://[::1]/x", "http://10.1.2.3/x", "file:///etc/passwd", "ftp://x"])
def test_blocked_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_valid_https_url_passes():
    assert validate_url("https://example.com/docs") == "https://example.com/docs"


def test_filename_traversal_rejected():
    with pytest.raises(ValueError):
        validate_filename("../../etc/passwd.pdf")
    with pytest.raises(ValueError):
        validate_filename("evil.exe")


def test_magic_bytes():
    assert sniff_file_signature(b"%PDF-1.4", ".pdf")
    assert not sniff_file_signature(b"not a pdf", ".pdf")
    assert sniff_file_signature(b"hello world", ".txt")


def test_evidence_dedupe_by_url():
    evs = [
        Evidence(source_id="a", title="same", url="https://x.com/a"),
        Evidence(source_id="b", title="same", url="https://x.com/a"),
        Evidence(source_id="c", title="other", url="https://y.com/b"),
    ]
    assert len(dedupe_evidence(evs)) == 2


def test_quality_inference():
    assert infer_quality("https://langchain.com/docs") == "official documentation"
    assert infer_quality("https://arxiv.org/abs/2301.1") == "academic"
    assert infer_quality("https://en.wikipedia.org/wiki/AI") == "wikipedia"
    assert infer_quality("https://support.microsoft.com/help/123") == "company documentation"


def test_quality_labels_reflect_authority():
    assert source_label("official documentation") == "official documentation (primary)"
    assert source_label("wikipedia") == "encyclopedia (secondary reference)"
    assert source_label("company documentation") == "company documentation (secondary)"
    assert source_label("community discussion") == "community discussion (community)"


def test_source_authority_priority_matches_preference():
    # official docs outrank papers, which outrank blog/wiki/community
    from app.services.citations import source_authority

    assert source_authority("official documentation") == "primary"
    assert source_authority("academic") == "primary"
    assert source_authority("technical blog") == "secondary"
    assert source_authority("wikipedia") == "secondary"
    assert source_authority("community discussion") == "community"


def test_citations_and_markers():
    evs = [
        Evidence(source_id="1", title="Docs A", url="https://a.com", relevant_text="x"),
        Evidence(source_id="2", title="Docs B", url="https://b.com", relevant_text="y"),
    ]
    cits = build_citations(evs)
    assert len(cits) == 2

    # only 2 valid citations exist
    invalid = validate_markers("See [1] and [5] for details.", 2)
    assert invalid == {5}

    out = attach_sources_section("Answer text [1]", cits)
    assert "## Sources" in out
    assert "https://a.com" in out