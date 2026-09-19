"""Book document generation: HTML and pure-Python EPUB 3.

Produces:
1. Semantic HTML5 document with print/PDF CSS formatting (WeasyPrint-ready).
2. Pure-Python EPUB 3 container with strict adherence to the IDPF EPUB 3 standard:
   - uncompressed 'mimetype' stored first;
   - META-INF/container.xml;
   - OEBPS package with manifest, spine, nav.xhtml, and valid XHTML chapters.
"""

from __future__ import annotations

import html
import io
import re
import uuid
import zipfile
from typing import Any

CSS_STYLES = """\
@page {
  size: A5;
  margin: 20mm 15mm 20mm 15mm;
  @bottom-center {
    content: counter(page);
    font-family: 'PT Serif', Georgia, serif;
    font-size: 9pt;
    color: #555;
  }
}

body {
  font-family: 'PT Serif', Georgia, 'Times New Roman', serif;
  font-size: 11pt;
  line-height: 1.6;
  color: #1a1a1a;
  margin: 0;
  padding: 0;
}

.title-page {
  page-break-after: always;
  text-align: center;
  padding-top: 60mm;
}

.title-page h1 {
  font-size: 26pt;
  font-weight: normal;
  margin-bottom: 8mm;
  color: #111;
}

.title-page h2 {
  font-size: 14pt;
  font-style: italic;
  font-weight: normal;
  color: #444;
  margin-bottom: 15mm;
}

.title-page .theme {
  font-size: 11pt;
  color: #666;
  max-width: 80%;
  margin: 0 auto;
}

.epigraph {
  page-break-after: always;
  padding-top: 50mm;
  font-style: italic;
  text-align: right;
  max-width: 70%;
  margin-left: auto;
}

.toc {
  page-break-after: always;
  padding-top: 20mm;
}

.toc h2 {
  text-align: center;
  font-size: 16pt;
  margin-bottom: 10mm;
}

.toc-item {
  margin-bottom: 3mm;
}

.toc-item a {
  text-decoration: none;
  color: #1a1a1a;
}

.chapter {
  page-break-before: always;
  padding-top: 15mm;
}

.chapter h2 {
  font-size: 18pt;
  font-weight: normal;
  text-align: center;
  margin-bottom: 10mm;
  color: #111;
}

p {
  margin-top: 0;
  margin-bottom: 3.5mm;
  text-indent: 5mm;
  text-align: justify;
}

p.no-indent {
  text-indent: 0;
}
"""


def _text_to_paragraphs(text: str) -> list[str]:
    """Convert raw text into clean paragraphs."""
    raw_paras = re.split(r"\n\s*\n", text.strip())
    paras = []
    for p in raw_paras:
        cleaned = " ".join(p.split())
        if cleaned:
            paras.append(cleaned)
    return paras


def build_book_html(
    *,
    title: str,
    subtitle: str | None = None,
    central_theme: str | None = None,
    epigraph: str | None = None,
    language: str = "ru",
    chapters: list[dict[str, Any]],
) -> str:
    """Build a complete, standalone semantic HTML5 document."""
    escaped_title = html.escape(title)
    escaped_subtitle = html.escape(subtitle) if subtitle else None
    escaped_theme = html.escape(central_theme) if central_theme else None
    escaped_epigraph = html.escape(epigraph) if epigraph else None

    lines = [
        "<!DOCTYPE html>",
        f'<html lang="{language}">',
        "<head>",
        '  <meta charset="utf-8"/>',
        f"  <title>{escaped_title}</title>",
        f"  <style>\n{CSS_STYLES}\n  </style>",
        "</head>",
        "<body>",
        '  <div class="title-page">',
        f"    <h1>{escaped_title}</h1>",
    ]

    if escaped_subtitle:
        lines.append(f"    <h2>{escaped_subtitle}</h2>")
    if escaped_theme:
        lines.append(f'    <div class="theme">{escaped_theme}</div>')
    lines.append("  </div>")

    if escaped_epigraph:
        lines.append('  <div class="epigraph">')
        lines.append(f"    <p>{escaped_epigraph}</p>")
        lines.append("  </div>")

    # Table of contents
    lines.append('  <div class="toc">')
    lines.append("    <h2>Содержание</h2>")
    for ch in chapters:
        ch_num = ch.get("chapter_number", 1)
        ch_title = html.escape(ch.get("title", f"Глава {ch_num}"))
        lines.append(
            f'    <div class="toc-item"><a href="#ch-{ch_num}">Глава {ch_num}. {ch_title}</a></div>'
        )
    lines.append("  </div>")

    # Chapters
    for ch in chapters:
        ch_num = ch.get("chapter_number", 1)
        ch_title = html.escape(ch.get("title", f"Глава {ch_num}"))
        ch_text = ch.get("final_text") or ch.get("text") or ""
        paras = _text_to_paragraphs(ch_text)

        lines.append(f'  <section class="chapter" id="ch-{ch_num}">')
        lines.append(f"    <h2>Глава {ch_num}. {ch_title}</h2>")
        for idx, p in enumerate(paras):
            cls = ' class="no-indent"' if idx == 0 else ""
            lines.append(f"    <p{cls}>{html.escape(p)}</p>")
        lines.append("  </section>")

    lines.append("</body>")
    lines.append("</html>")
    return "\n".join(lines)


def write_epub3(
    *,
    title: str,
    subtitle: str | None = None,
    central_theme: str | None = None,
    epigraph: str | None = None,
    language: str = "ru",
    chapters: list[dict[str, Any]],
    book_id: str | None = None,
) -> bytes:
    """Generate a valid, pure-Python EPUB 3 container."""
    uid = book_id or f"urn:uuid:{uuid.uuid4()}"
    escaped_title = html.escape(title)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # 1. mimetype: MUST be first file, uncompressed
        zf.writestr("mimetype", b"application/epub+zip", compress_type=zipfile.ZIP_STORED)

        # 2. META-INF/container.xml
        container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
        zf.writestr("META-INF/container.xml", container_xml.encode("utf-8"))

        # 3. OEBPS/style.css
        zf.writestr("OEBPS/style.css", CSS_STYLES.encode("utf-8"))

        # 4. Title page XHTML
        title_page_xhtml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}">
<head>
  <meta charset="utf-8"/>
  <title>{escaped_title}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <div class="title-page">
    <h1>{escaped_title}</h1>
    {"<h2>" + html.escape(subtitle) + "</h2>" if subtitle else ""}
    {"<div class='theme'>" + html.escape(central_theme) + "</div>" if central_theme else ""}
  </div>
</body>
</html>"""
        zf.writestr("OEBPS/title.xhtml", title_page_xhtml.encode("utf-8"))

        # 5. Chapters XHTML
        chapter_manifest_items = []
        chapter_spine_items = []

        for ch in chapters:
            ch_num = ch.get("chapter_number", 1)
            ch_title = html.escape(ch.get("title", f"Глава {ch_num}"))
            ch_text = ch.get("final_text") or ch.get("text") or ""
            paras = _text_to_paragraphs(ch_text)

            ch_xhtml_lines = [
                '<?xml version="1.0" encoding="UTF-8"?>',
                "<!DOCTYPE html>",
                f'<html xmlns="http://www.w3.org/1999/xhtml" lang="{language}">',
                "<head>",
                '  <meta charset="utf-8"/>',
                f"  <title>{ch_title}</title>",
                '  <link rel="stylesheet" type="text/css" href="style.css"/>',
                "</head>",
                "<body>",
                f'  <section class="chapter" id="ch-{ch_num}">',
                f"    <h2>Глава {ch_num}. {ch_title}</h2>",
            ]
            for idx, p in enumerate(paras):
                cls = ' class="no-indent"' if idx == 0 else ""
                ch_xhtml_lines.append(f"    <p{cls}>{html.escape(p)}</p>")
            ch_xhtml_lines.append("  </section>")
            ch_xhtml_lines.append("</body>")
            ch_xhtml_lines.append("</html>")

            filename = f"chapter_{ch_num}.xhtml"
            item_id = f"ch_{ch_num}"
            zf.writestr(f"OEBPS/{filename}", "\n".join(ch_xhtml_lines).encode("utf-8"))

            chapter_manifest_items.append(
                f'<item id="{item_id}" href="{filename}" media-type="application/xhtml+xml"/>'
            )
            chapter_spine_items.append(f'<itemref idref="{item_id}"/>')

        # 6. OEBPS/nav.xhtml (EPUB 3 Table of Contents)
        nav_lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!DOCTYPE html>",
            (
                '<html xmlns="http://www.w3.org/1999/xhtml" '
                f'xmlns:epub="http://www.idpf.org/2007/ops" lang="{language}">'
            ),
            "<head>",
            '  <meta charset="utf-8"/>',
            "  <title>Содержание</title>",
            '  <link rel="stylesheet" type="text/css" href="style.css"/>',
            "</head>",
            "<body>",
            '  <nav epub:type="toc" id="toc">',
            "    <h1>Содержание</h1>",
            "    <ol>",
            '      <li><a href="title.xhtml">Титул</a></li>',
        ]
        for ch in chapters:
            ch_num = ch.get("chapter_number", 1)
            ch_title = html.escape(ch.get("title", f"Глава {ch_num}"))
            nav_lines.append(
                f'      <li><a href="chapter_{ch_num}.xhtml">Глава {ch_num}. {ch_title}</a></li>'
            )
        nav_lines.append("    </ol>")
        nav_lines.append("  </nav>")
        nav_lines.append("</body>")
        nav_lines.append("</html>")
        zf.writestr("OEBPS/nav.xhtml", "\n".join(nav_lines).encode("utf-8"))

        # 7. OEBPS/content.opf
        manifest_str = "\n    ".join(
            [
                '<item id="style" href="style.css" media-type="text/css"/>',
                (
                    '<item id="nav" href="nav.xhtml" '
                    'media-type="application/xhtml+xml" properties="nav"/>'
                ),
                '<item id="titlepage" href="title.xhtml" media-type="application/xhtml+xml"/>',
                *chapter_manifest_items,
            ]
        )
        spine_str = "\n    ".join(['<itemref idref="titlepage"/>', *chapter_spine_items])

        content_opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{uid}</dc:identifier>
    <dc:title>{escaped_title}</dc:title>
    <dc:language>{language}</dc:language>
    <dc:publisher>MURA Platform</dc:publisher>
    <meta property="dcterms:modified">2026-09-18T12:00:00Z</meta>
  </metadata>
  <manifest>
    {manifest_str}
  </manifest>
  <spine>
    {spine_str}
  </spine>
</package>"""
        zf.writestr("OEBPS/content.opf", content_opf.encode("utf-8"))

    return buf.getvalue()
