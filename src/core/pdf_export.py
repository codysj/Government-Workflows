"""Dependency-free PDF summary exporter (Tier 1 roadmap #5: PDF export).

The review-packet exporter already writes Markdown and JSON. Some reviewers and
records requests want a single self-contained **PDF**. Rather than pull in a
third-party PDF library, this module writes a MINIMAL but structurally valid
PDF 1.4 file by hand: a clean, monospace, multi-page **text** PDF that opens in
standard viewers (Acrobat, Preview, browsers).

Scope and limitations (by design):

  * Text only — one base-14 font, **Courier** (no font embedding, no images,
    no colors, no tables/markup rendering). Markdown is flattened to plain text.
  * Latin-1/ASCII only — characters that Latin-1 cannot encode are replaced
    with ``?`` so the byte stream never corrupts.
  * Long lines are wrapped to a fixed character width and content is paginated
    by a fixed number of lines per page.

A PDF is a set of numbered *objects* followed by a cross-reference (*xref*)
table that records each object's **byte offset** from the start of the file,
and a *trailer* pointing at the xref and the document root. We build the object
bodies first, concatenate them while recording offsets, then emit the xref and
trailer. Everything is deterministic and never calls an LLM.
"""
from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# Layout constants (US Letter, 72 pt/inch). Courier is ~0.6 em wide per glyph;
# at the chosen font size these give comfortable margins on a standard page.
# --------------------------------------------------------------------------- #
PAGE_WIDTH = 612          # 8.5 in * 72
PAGE_HEIGHT = 792         # 11  in * 72
MARGIN = 54               # 0.75 in
MAX_CHARS = 90            # characters per wrapped line (fits within margins)
LINES_PER_PAGE = 56       # text lines per page before paginating


# --------------------------------------------------------------------------- #
# Text preparation
# --------------------------------------------------------------------------- #
def _escape(text: str) -> str:
    """Escape the three PDF string metacharacters: ``\\ ( )``.

    Backslash must be escaped first so the escapes we add are not re-escaped.
    """
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _to_latin1(text: str) -> str:
    """Force text into Latin-1, replacing un-encodable chars with ``?``.

    The base-14 fonts use single-byte encodings, so any character outside
    Latin-1 cannot be represented; replacing keeps the byte stream valid.
    """
    return text.encode("latin-1", "replace").decode("latin-1")


def _wrap_line(line: str, width: int) -> list[str]:
    """Hard-wrap a single logical line to ``width`` characters.

    Wrapping is by character count (monospace), not by words, so the output is
    predictable and never overflows the page. Empty lines are preserved.
    """
    if line == "":
        return [""]
    return [line[i:i + width] for i in range(0, len(line), width)]


def _paginate(text: str, max_chars: int, lines_per_page: int) -> list[list[str]]:
    """Wrap + split text into pages, each a list of already-wrapped lines."""
    wrapped: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        wrapped.extend(_wrap_line(_to_latin1(raw), max_chars))
    if not wrapped:
        wrapped = [""]
    return [wrapped[i:i + lines_per_page]
            for i in range(0, len(wrapped), lines_per_page)]


# --------------------------------------------------------------------------- #
# PDF object construction
# --------------------------------------------------------------------------- #
def _content_stream(lines: list[str], font_size: int) -> bytes:
    """Build a page content stream that prints ``lines`` top-to-bottom.

    ``BT``/``ET`` delimit a text object; ``Tf`` selects the font; ``Td`` sets
    the start position; ``TL`` sets the leading (line height) so each ``T*``
    advances one line; each line is shown with ``Tj``.
    """
    leading = font_size + 2
    start_y = PAGE_HEIGHT - MARGIN - font_size
    ops = [
        "BT",
        f"/F1 {font_size} Tf",
        f"{leading} TL",
        f"{MARGIN} {start_y} Td",
    ]
    for ln in lines:
        ops.append(f"({_escape(ln)}) Tj")
        ops.append("T*")
    ops.append("ET")
    return "\n".join(ops).encode("latin-1")


def text_to_pdf(
    text: str,
    out_path: str | Path,
    *,
    title: str | None = None,
    font_size: int = 9,
) -> Path:
    """Render ``text`` to a structurally valid single-font (Courier) PDF.

    Long lines are wrapped to :data:`MAX_CHARS` characters and content is
    paginated to :data:`LINES_PER_PAGE` lines per page, so long input yields
    multiple ``/Page`` objects under a shared ``/Pages`` tree. An optional
    ``title`` is prepended as a heading line (and set as the document title).
    Returns the written path.
    """
    out_path = Path(out_path)
    body = f"{title}\n\n{text}" if title else text
    pages = _paginate(body, MAX_CHARS, font_size and LINES_PER_PAGE)

    # Fixed objects: 1=Catalog, 2=Pages, 3=Font. Then per page a Page object and
    # its content-stream object. We assign object numbers up front so /Kids and
    # /Pages parent references are correct.
    n_pages = len(pages)
    pages_obj_num = 2
    font_obj_num = 3
    first_page_obj = 4
    # Each page uses two objects: the /Page dict and its content stream.
    page_nums = [first_page_obj + 2 * i for i in range(n_pages)]
    content_nums = [first_page_obj + 2 * i + 1 for i in range(n_pages)]

    objects: dict[int, bytes] = {}

    objects[1] = (
        f"<< /Type /Catalog /Pages {pages_obj_num} 0 R >>".encode("latin-1"))

    kids = " ".join(f"{p} 0 R" for p in page_nums)
    objects[pages_obj_num] = (
        f"<< /Type /Pages /Count {n_pages} /Kids [{kids}] >>".encode("latin-1"))

    objects[font_obj_num] = (
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier "
        b"/Encoding /WinAnsiEncoding >>")

    for i, page_lines in enumerate(pages):
        stream = _content_stream(page_lines, font_size)
        objects[page_nums[i]] = (
            f"<< /Type /Page /Parent {pages_obj_num} 0 R "
            f"/MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
            f"/Contents {content_nums[i]} 0 R >>").encode("latin-1")
        objects[content_nums[i]] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("latin-1")
            + stream + b"\nendstream")

    # ------------------------------------------------------------------- #
    # Serialize: header, then each object, recording byte offsets for xref.
    # ------------------------------------------------------------------- #
    out = bytearray(b"%PDF-1.4\n")
    # A binary comment marks the file as containing binary data (convention).
    out += b"%\xe2\xe3\xcf\xd3\n"

    max_obj = max(objects)
    offsets: dict[int, int] = {}
    for num in range(1, max_obj + 1):
        body_bytes = objects[num]
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode("latin-1")
        out += body_bytes
        out += b"\nendobj\n"

    # Cross-reference table: one 20-byte entry per object (object 0 is the free
    # head). Each in-use entry is "<10-digit offset> <5-digit gen> n\r\n".
    xref_pos = len(out)
    out += b"xref\n"
    out += f"0 {max_obj + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for num in range(1, max_obj + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode("latin-1")

    trailer = (f"trailer\n<< /Size {max_obj + 1} /Root 1 0 R >>\n"
               f"startxref\n{xref_pos}\n%%EOF\n")
    out += trailer.encode("latin-1")

    out_path.write_bytes(bytes(out))
    return out_path


# --------------------------------------------------------------------------- #
# Markdown / review-packet wrappers
# --------------------------------------------------------------------------- #
def markdown_to_pdf(md: str, out_path: str | Path, **kw) -> Path:
    """Flatten light markdown to plain text, then render it via :func:`text_to_pdf`.

    Only a *light* conversion: leading heading hashes (``#``) and blockquote
    markers (``>``) are stripped, while table pipes (``|``) and ``- `` bullets
    are kept verbatim as text. No bold/italic/link rendering.
    """
    plain: list[str] = []
    for raw in md.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw
        stripped = line.lstrip()
        # Strip a leading run of '#'/'>' markers (and the single space after).
        if stripped[:1] in ("#", ">"):
            j = 0
            while j < len(stripped) and stripped[j] in "#>":
                j += 1
            if j < len(stripped) and stripped[j] == " ":
                j += 1
            line = stripped[j:]
        plain.append(line)
    return text_to_pdf("\n".join(plain), out_path, **kw)


def review_packet_pdf(
    markdown_text: str,
    out_path: str | Path,
    *,
    title: str = "Review Packet",
) -> Path:
    """Thin wrapper: render a review-packet markdown string to a titled PDF."""
    return markdown_to_pdf(markdown_text, out_path, title=title)


__all__ = [
    "text_to_pdf",
    "markdown_to_pdf",
    "review_packet_pdf",
    "MAX_CHARS",
    "LINES_PER_PAGE",
]
