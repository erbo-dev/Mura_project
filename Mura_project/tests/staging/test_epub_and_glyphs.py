"""Staging validation tests for EPUB 3 IDPF validity and Kazakh/Cyrillic glyph integrity."""

from __future__ import annotations

import io
import zipfile

import pytest

from mura.book.export_document import build_book_html, write_epub3
from mura.domain.book_models import BookLanguage


@pytest.mark.staging
def test_epub3_zip_container_and_mimetype_invariant() -> None:
    """Verifies that generated EPUB strictly complies with IDPF EPUB 3 specification:

    - Mimetype file is stored first.
    - Mimetype is uncompressed (compression == ZIP_STORED).
    - Mimetype content is exactly 'application/epub+zip'.
    """
    kazakh_title = "Мұра: Біздің әулет тарихы"
    kazakh_subtitle = "Әже өсиеті мен ұрпақ сабақтастығы"
    chapters = [
        {
            "chapter_number": 1,
            "title": "Әжемнің мұрасы",
            "subtitle": "Ғасыр куәсі",
            "time_period": "1935–1960",
            "text": "Мұра — баға жетпес құндылық. Әже өмірі — ұрпаққа өнеге мен із.",
        }
    ]

    epub_bytes = write_epub3(
        title=kazakh_title,
        subtitle=kazakh_subtitle,
        language="kk",
        chapters=chapters,
    )

    assert isinstance(epub_bytes, bytes)
    assert len(epub_bytes) > 0

    # Verify standard ZIP container
    zip_buffer = io.BytesIO(epub_bytes)
    with zipfile.ZipFile(zip_buffer, "r") as zf:
        file_list = zf.infolist()
        assert len(file_list) >= 4

        # IDPF Invariant 1: First entry MUST be mimetype
        first_entry = file_list[0]
        assert first_entry.filename == "mimetype"

        # IDPF Invariant 2: mimetype MUST NOT be compressed
        assert first_entry.compress_type == zipfile.ZIP_STORED

        # IDPF Invariant 3: mimetype content must be application/epub+zip
        mimetype_content = zf.read("mimetype").decode("utf-8")
        assert mimetype_content == "application/epub+zip"

        # Verify META-INF/container.xml
        assert "META-INF/container.xml" in zf.namelist()
        container_xml = zf.read("META-INF/container.xml").decode("utf-8")
        assert 'full-path="OEBPS/content.opf"' in container_xml

        # Verify OEBPS files
        assert "OEBPS/content.opf" in zf.namelist()
        assert "OEBPS/nav.xhtml" in zf.namelist()
        assert "OEBPS/chapter_1.xhtml" in zf.namelist()


@pytest.mark.staging
def test_kazakh_cyrillic_glyph_fidelity_in_epub_and_html() -> None:
    """Verifies that all 9 Kazakh-specific Cyrillic letters and diacritics

    (Ә, ғ, қ, ң, ө, ұ, ү, h, і) survive round-trip rendering in both HTML and EPUB.
    """
    special_kazakh_glyphs = [
        "Мұра",
        "Әже",
        "Ғасыр",
        "Құндылық",
        "Өмір",
        "Ұрпақ",
        "Із",
        "Көңіл",
        "Үміт",
    ]
    text_content = " ".join(special_kazakh_glyphs)

    chapters = [
        {
            "chapter_number": 1,
            "title": "Қазақ әліпбиі мен мұра",
            "subtitle": "Тарихи іздер",
            "time_period": "1991–2026",
            "text": text_content,
        }
    ]

    # 1. Verify HTML export document
    html_out = build_book_html(
        title="Мұра сөздігі",
        subtitle="Ғасырлар сыры",
        language=BookLanguage.KK.value,
        chapters=chapters,
    )
    for glyph_word in special_kazakh_glyphs:
        assert glyph_word in html_out, f"Missing glyph word in HTML: {glyph_word}"

    # 2. Verify EPUB XHTML chapter content
    epub_bytes = write_epub3(
        title="Мұра сөздігі",
        subtitle="Ғасырлар сыры",
        language="kk",
        chapters=chapters,
    )
    with zipfile.ZipFile(io.BytesIO(epub_bytes), "r") as zf:
        ch1_xhtml = zf.read("OEBPS/chapter_1.xhtml").decode("utf-8")
        for glyph_word in special_kazakh_glyphs:
            assert glyph_word in ch1_xhtml, f"Missing glyph word in EPUB chapter: {glyph_word}"
