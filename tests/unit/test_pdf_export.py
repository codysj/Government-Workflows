"""Unit tests for the dependency-free PDF exporter (src.core.pdf_export)."""
from __future__ import annotations

from src.core.pdf_export import (
    markdown_to_pdf,
    review_packet_pdf,
    text_to_pdf,
)


def test_basic_pdf_is_structurally_valid(tmp_path):
    out = text_to_pdf("Hello world\nLine two", tmp_path / "basic.pdf")
    assert out.exists()
    data = out.read_bytes()
    # Begins with the PDF 1.4 marker and ends with the EOF trailer.
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    # Has the core structural pieces.
    assert b"xref" in data
    assert b"trailer" in data
    assert b"startxref" in data
    assert b"/Type /Catalog" in data


def test_long_input_paginates_to_multiple_pages(tmp_path):
    text = "\n".join(f"line number {i}" for i in range(400))
    out = text_to_pdf(text, tmp_path / "long.pdf")
    data = out.read_bytes()
    # More than one /Page object, and the /Pages /Count reflects it.
    assert data.count(b"/Type /Page\n") > 1 or data.count(b"/Type /Page ") > 1
    count_idx = data.index(b"/Count ")
    count_val = int(data[count_idx + len(b"/Count "):count_idx + 20].split()[0])
    assert count_val > 1


def test_special_chars_do_not_corrupt_output(tmp_path):
    # Parens, backslash, and a non-latin char (em dash / CJK) must be handled.
    text = "weird (parens) and \\backslash\\ and unicode — 中 end"
    out = text_to_pdf(text, tmp_path / "special.pdf")
    data = out.read_bytes()
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    # The escaped paren sequences are present in the content stream.
    assert b"\\(parens\\)" in data


def test_long_wrapped_line_paginates(tmp_path):
    # A single very long line wraps into many lines, forcing multiple pages.
    out = text_to_pdf("x" * 20000, tmp_path / "wide.pdf")
    data = out.read_bytes()
    count_idx = data.index(b"/Count ")
    count_val = int(data[count_idx + len(b"/Count "):count_idx + 20].split()[0])
    assert count_val > 1


def test_markdown_strips_leading_hash(tmp_path):
    out = markdown_to_pdf("# Heading One\n\nbody text", tmp_path / "md.pdf")
    data = out.read_bytes()
    # The '# ' prefix is stripped; the heading text survives, the marker does not.
    assert b"(Heading One)" in data
    assert b"(# Heading One)" not in data
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")


def test_markdown_keeps_tables_and_bullets(tmp_path):
    md = "> quote\n| a | b |\n- bullet item"
    out = markdown_to_pdf(md, tmp_path / "mdkeep.pdf")
    data = out.read_bytes()
    assert b"(| a | b |)" in data
    assert b"(- bullet item)" in data
    assert b"(quote)" in data  # leading '> ' stripped


def test_review_packet_pdf_uses_title(tmp_path):
    out = review_packet_pdf("# Run\n\nsome findings", tmp_path / "packet.pdf",
                            title="My Packet")
    data = out.read_bytes()
    assert out.exists()
    assert b"(My Packet)" in data
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
