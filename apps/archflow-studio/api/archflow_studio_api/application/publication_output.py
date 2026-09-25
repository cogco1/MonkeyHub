"""Two output compilers over the same retained page coordinates and text lines."""
from io import BytesIO
import re
from PIL import Image
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader
from pptx import Presentation
from pptx.util import Pt
from pptx.enum.text import MSO_ANCHOR
from ..transport.errors import StudioError
from .artifacts import document_bytes
from .boards import _page_raster, BoardExport
from .publications import read_publication

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))


def _lines(text, width, size):
    lines = []
    for paragraph in text.split("\n"):
        current = ""
        for token in re.findall(r"[A-Za-z0-9_]+|[^A-Za-z0-9_]", paragraph):
            if current and pdfmetrics.stringWidth(current + token, FONT, size) > width:
                lines.append(current.rstrip())
                current = ""
            for character in token:
                if current and pdfmetrics.stringWidth(current + character, FONT, size) > width:
                    lines.append(current)
                    current = ""
                current += character
        lines.append(current.rstrip())
    return lines


def _image(binding, item):
    source = item["source"]
    document, data = document_bytes(binding, source["runId"], source["assetSha256"], source.get("revisionRef"))
    data = _page_raster(data, document.mime_type, source["pageIndex"], "png", 2048)
    with Image.open(BytesIO(data)) as original:
        left, top, right, bottom = item["crop"]
        box = (round(left * original.width), round(top * original.height), round((1 - right) * original.width), round((1 - bottom) * original.height))
        if box[0] >= box[2] or box[1] >= box[3]:
            raise StudioError(422, "PUBLICATION_CROP", "The crop leaves no visible pixels.")
        image = original.crop(box)
        scale = min(item["width"] / image.width, item["height"] / image.height)
        width, height = image.width * scale, image.height * scale
        output = BytesIO()
        image.save(output, format="PNG")
    return output.getvalue(), item["x"] + (item["width"] - width) / 2, item["y"] + (item["height"] - height) / 2, width, height


def export_publication(binding, revision, format):
    publication = read_publication(binding, revision)
    if not publication["pages"]:
        raise StudioError(422, "PUBLICATION_EMPTY", "Add a page before exporting.")
    if any(row["status"] == "missing" for row in publication["sources"]):
        raise StudioError(409, "PUBLICATION_SOURCE_MISSING", "Restore the missing sources or remove their elements before exporting.")
    width, height = publication["spec"]["width"], publication["spec"]["height"]
    output = BytesIO()
    pdf = Canvas(output, pagesize=(width, height), invariant=1, pageCompression=1) if format == "pdf" else None
    ppt = Presentation() if format == "pptx" else None
    if pdf:
        pdf.setTitle(publication["title"])
        pdf.setAuthor("MonkeyHub")
    else:
        ppt.slide_width, ppt.slide_height = Pt(width), Pt(height)
        ppt.core_properties.title = publication["title"]
        ppt.core_properties.author = "MonkeyHub"
    for page in publication["pages"]:
        slide = ppt.slides.add_slide(ppt.slide_layouts[6]) if ppt else None
        for item in page["elements"]:
            x, y, w, h = (item[key] for key in ("x", "y", "width", "height"))
            if item["kind"] == "image":
                data, x, y, w, h = _image(binding, item)
                if pdf:
                    pdf.drawImage(ImageReader(BytesIO(data)), x, height - y - h, width=w, height=h)
                else:
                    picture = slide.shapes.add_picture(BytesIO(data), Pt(x), Pt(y), Pt(w), Pt(h))
                    picture.name = item["id"]
                continue
            size = item["fontSize"]
            lines = _lines(item["text"], w, size)
            if len(lines) * size * 1.2 > h:
                raise StudioError(422, "PUBLICATION_TEXT_OVERFLOW", f"Text in {item['id']} does not fit. Enlarge the box or shorten the text.")
            if pdf:
                pdf.setFillColorRGB(.08, .08, .09)
                pdf.setFont(FONT, size)
                for index, line in enumerate(lines):
                    pdf.drawString(x, height - y - size - index * size * 1.2, line)
            else:
                shape = slide.shapes.add_textbox(Pt(x), Pt(y), Pt(w), Pt(h))
                shape.name = item["id"]
                frame = shape.text_frame
                frame.margin_left = frame.margin_right = frame.margin_top = frame.margin_bottom = 0
                frame.word_wrap = False
                frame.vertical_anchor = MSO_ANCHOR.TOP
                for index, line in enumerate(lines):
                    paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
                    paragraph.text = line
                    paragraph.font.name = "Microsoft YaHei"
                    paragraph.font.size = Pt(size)
                    paragraph.line_spacing = Pt(size * 1.2)
                    paragraph.space_before = paragraph.space_after = Pt(0)
        if pdf:
            pdf.showPage()
    if pdf:
        pdf.save()
    else:
        ppt.save(output)
    name = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", publication["title"]).strip("-") or "publication"
    return BoardExport(name + "." + format,
        "application/pdf" if pdf else "application/vnd.openxmlformats-officedocument.presentationml.presentation", output.getvalue())
