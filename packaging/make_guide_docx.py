"""Builds docs/GETTING-STARTED.docx from docs/GETTING-STARTED.md (screenshots included).

Handles the Markdown the guide uses: headings, paragraphs, bullet / numbered lists (one level of nesting),
> notes, images, --- rules, **bold**, *italic*, `code` and [links](...).
Needs python-docx:  python -m pip install python-docx   then   python packaging\\make_guide_docx.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "GETTING-STARTED.md"
OUT = ROOT / "docs" / "GETTING-STARTED.docx"
GREEN, DARK = RGBColor(0x3E, 0x8E, 0x1F), RGBColor(0x1B, 0x26, 0x3B)
INLINE = re.compile(r"(\*\*.+?\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|(?<![\w*])\*[^*\s][^*]*?\*(?![\w*]))")


def add_inline(par, text: str, bold=False, italic=False):
    for part in INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            add_inline(par, part[2:-2], True, italic)
        elif part.startswith("`") and part.endswith("`"):
            r = par.add_run(part[1:-1]); r.font.name = "Consolas"; r.bold, r.italic = bold, italic
        elif part.startswith("[") and "](" in part:
            label, url = re.match(r"\[([^\]]+)\]\(([^)]+)\)", part).groups()
            if url.startswith("http"):
                add_link(par, label, url)
            else:   # a link to another file in the folder - name it instead
                add_inline(par, f"{label} ({Path(url).name})", bold, italic)
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            add_inline(par, part[1:-1], bold, True)
        else:
            r = par.add_run(part); r.bold, r.italic = bold, italic


def add_link(par, text: str, url: str):
    rid = par.part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                             is_external=True)
    link = OxmlElement("w:hyperlink"); link.set(qn("r:id"), rid)
    run = OxmlElement("w:r"); rpr = OxmlElement("w:rPr")
    for tag, val in (("w:color", "0563C1"), ("w:u", "single")):
        e = OxmlElement(tag); e.set(qn("w:val"), val); rpr.append(e)
    run.append(rpr); t = OxmlElement("w:t"); t.text = text; t.set(qn("xml:space"), "preserve"); run.append(t)
    link.append(run); par._p.append(link)


def shade(par, fill: str):
    ppr = par._p.get_or_add_pPr()
    shd = OxmlElement("w:shd"); shd.set(qn("w:val"), "clear"); shd.set(qn("w:color"), "auto"); shd.set(qn("w:fill"), fill)
    ppr.append(shd)
    bdr = OxmlElement("w:pBdr"); left = OxmlElement("w:left")
    for k, v in (("w:val", "single"), ("w:sz", "24"), ("w:space", "8"), ("w:color", "97CE4C")):
        left.set(qn(k), v)
    bdr.append(left); ppr.append(bdr)


def rule(doc):
    p = doc.add_paragraph(); ppr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr"); bot = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "6"), ("w:space", "1"), ("w:color", "BFBFBF")):
        bot.set(qn(k), v)
    bdr.append(bot); ppr.append(bdr)


def image(doc, alt: str, path: Path):
    w, h = Image.open(path).size
    width = Inches(2.6) if h > w else Inches(6.0)          # phone screenshots stay phone-sized
    p = doc.add_paragraph(); p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(path), width=width)
    cap = doc.add_paragraph(); cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cap.add_run(alt); r.italic = True; r.font.size = Pt(9); r.font.color.rgb = RGBColor(0x59, 0x59, 0x59)


def build() -> int:
    doc = Document()
    st = doc.styles["Normal"]; st.font.name = "Calibri"; st.font.size = Pt(11)
    for name, color in (("Title", DARK), ("Heading 1", GREEN), ("Heading 2", GREEN), ("Heading 3", DARK)):
        doc.styles[name].font.color.rgb = color
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Inches(1)

    lines = SRC.read_text("utf-8").splitlines()
    i, para = 0, []

    def flush():
        if para:
            add_inline(doc.add_paragraph(), " ".join(s.strip() for s in para)); para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        m_img = re.fullmatch(r"!\[([^\]]*)\]\(([^)]+)\)", s)
        m_head = re.match(r"(#{1,3}) (.+)", line)
        m_list = re.match(r"( *)(- |\d+\. )(.*)", line)
        if not s:
            flush()
        elif s == "---":
            flush(); rule(doc)
        elif m_img:
            flush(); image(doc, m_img.group(1), SRC.parent / m_img.group(2))
        elif m_head:
            flush()
            level = len(m_head.group(1))
            text = re.sub(r"[*`]", "", m_head.group(2))
            doc.add_heading(text, 0 if level == 1 else level - 1)
        elif s.startswith(">"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip()); i += 1
            p = doc.add_paragraph(); shade(p, "F1F8E9"); add_inline(p, " ".join(quote))
            continue
        elif m_list:
            flush()
            indent, marker, text = m_list.groups()
            # continuation lines belong to this item
            while i + 1 < len(lines) and lines[i + 1].startswith(" ") and lines[i + 1].strip() \
                    and not re.match(r" *(- |\d+\. )", lines[i + 1]):
                i += 1; text += " " + lines[i].strip()
            nested = len(indent) >= 2
            if marker[0].isdigit():   # literal numbers: Word's List Number style would keep counting across lists
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Inches(0.85 if nested else 0.5)
                p.paragraph_format.first_line_indent = Inches(-0.25)
                p.paragraph_format.space_after = Pt(3)
                p.add_run(marker.strip() + "\t")
                add_inline(p, text)
            else:
                add_inline(doc.add_paragraph(style="List Bullet 2" if nested else "List Bullet"), text)
        else:
            para.append(line)
        i += 1
    flush()
    doc.core_properties.title = "Getting started with RickWire"
    doc.save(OUT)
    print(f"{OUT}  ({OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(build())
