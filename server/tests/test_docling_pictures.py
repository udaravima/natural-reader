"""Pictures a conversion keeps (images=embed|describe): the crops become files
beside the document and the Markdown refers to them, so the page text, the chunks
and the chat never carry image data. Uses Docling's real document type and export;
no model runs."""
from __future__ import annotations

import pytest

from server.services import docling_convert

pytest.importorskip("docling_core")
from docling_core.types.doc.base import BoundingBox, CoordOrigin, ImageRefMode, Size  # noqa: E402
from docling_core.types.doc.document import DoclingDocument, ImageRef, ProvenanceItem  # noqa: E402
from docling_core.types.doc.labels import DocItemLabel  # noqa: E402
from PIL import Image  # noqa: E402


def _doc() -> DoclingDocument:
    doc = DoclingDocument(name="t")
    doc.add_page(page_no=1, size=Size(width=600, height=800))
    box = BoundingBox(l=1, t=1, r=50, b=20, coord_origin=CoordOrigin.TOPLEFT)
    doc.add_text(label=DocItemLabel.TEXT, text="Hello",
                 prov=ProvenanceItem(page_no=1, bbox=box, charspan=(0, 5)))
    return doc


def _picture(doc, width, height, color="red"):
    box = BoundingBox(l=10, t=100, r=10 + width, b=100 + height, coord_origin=CoordOrigin.TOPLEFT)
    return doc.add_picture(image=ImageRef.from_pil(Image.new("RGB", (width, height), color), dpi=72),
                           prov=ProvenanceItem(page_no=1, bbox=box, charspan=(0, 0)))


def _markdown(doc):
    return doc.export_to_markdown(page_no=1, image_mode=ImageRefMode.REFERENCED)


def test_a_kept_picture_is_a_file_and_a_reference():
    doc = _doc()
    _picture(doc, 200, 100)
    pictures = docling_convert.collect_pictures(doc)
    assert len(pictures) == 1 and pictures[0].startswith(b"\x89PNG")
    assert "![Image](figures/1.png)" in _markdown(doc)
    assert "base64" not in _markdown(doc)


def test_a_tiny_picture_stays_a_placeholder():
    doc = _doc()
    _picture(doc, 91, 16)   # a footer logo, measured on a real PDF
    assert docling_convert.collect_pictures(doc) == []
    assert "<!-- image -->" in _markdown(doc)


def test_the_same_picture_on_every_page_is_one_file():
    doc = _doc()
    _picture(doc, 200, 100)
    _picture(doc, 200, 100)
    _picture(doc, 200, 100, color="blue")
    pictures = docling_convert.collect_pictures(doc)
    assert len(pictures) == 2
    assert _markdown(doc).count("figures/1.png") == 2 and _markdown(doc).count("figures/2.png") == 1


def test_past_the_cap_a_picture_stays_a_placeholder(monkeypatch):
    monkeypatch.setenv("DOCLING_MAX_PICTURES", "1")
    doc = _doc()
    _picture(doc, 200, 100)
    _picture(doc, 200, 100, color="blue")
    assert len(docling_convert.collect_pictures(doc)) == 1
    assert "figures/1.png" in _markdown(doc) and "<!-- image -->" in _markdown(doc)


@pytest.mark.parametrize("raw", ["nope", "-1"])
def test_a_bad_cap_is_the_default(monkeypatch, raw):
    monkeypatch.setenv("DOCLING_MAX_PICTURES", raw)
    assert docling_convert.max_pictures() == docling_convert.DEFAULT_MAX_PICTURES


def test_figure_references_are_not_text():
    md = "Before\n\n![Image](figures/12.png)\n\nAfter ![Image](figures/3.png)."
    assert docling_convert.strip_figure_refs(md) == "Before\n\nAfter ."
    # Somebody else's image syntax is the document's own text.
    assert docling_convert.strip_figure_refs("![x](https://example.com/a.png)") == "![x](https://example.com/a.png)"


@pytest.mark.parametrize("images, kept", [("drop", False), ("embed", True), ("describe", True)])
def test_the_converter_crops_pictures_only_when_it_keeps_them(images, kept):
    pytest.importorskip("docling")
    from docling.datamodel.base_models import InputFormat
    converter = docling_convert._build_converter("standard", False, False, images)
    options = converter.format_to_options[InputFormat.PDF].pipeline_options
    assert options.generate_picture_images is kept
    assert options.images_scale == (2.0 if kept else 1.0)
