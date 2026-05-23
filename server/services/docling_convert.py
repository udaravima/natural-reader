"""
Docling-based PDF → Markdown conversion service.

Wraps `docling.document_converter.DocumentConverter` and produces per-page
markdown so the existing per-page RAG / chunking pipeline stays the unit of
work. Conversion is synchronous inside docling (CPU- and IO-heavy: layout
models + table parsing + optional OCR), so we run it via `asyncio.to_thread`.

The first call lazily instantiates the converter and downloads model weights —
allow a few minutes the first time on a fresh machine.

Options dict shape (all keys optional):
    preset:     'fast' | 'standard' | 'accurate'    (default 'standard')
    ocr:        bool                                (force-enable OCR)
    tables:     bool                                (extract tables; default True)
    images:     'drop' | 'embed' | 'describe'       (default 'drop')
    page_range: [start, end]   1-based inclusive    (None = whole doc)

If the installed docling version doesn't expose a particular knob (page_range,
per-page export, VLM pipeline, etc.) we degrade gracefully rather than 500.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DOCLING_ENABLED = os.environ.get("DOCLING_ENABLED", "false").lower() in ("1", "true", "yes")

# Lazy module-level cache: building a DocumentConverter is expensive (downloads
# models). We keep one instance per pipeline preset.
_converters: dict[str, Any] = {}
_converter_lock = asyncio.Lock()


def is_enabled() -> bool:
    return DOCLING_ENABLED


def _normalize_options(options: dict | None) -> dict:
    opts = dict(options or {})
    preset = (opts.get("preset") or "standard").lower()
    if preset not in ("fast", "standard", "accurate"):
        preset = "standard"
    opts["preset"] = preset
    opts.setdefault("ocr", False)
    opts.setdefault("tables", True)
    img = opts.get("images") or "drop"
    if img not in ("drop", "embed", "describe"):
        img = "drop"
    opts["images"] = img
    pr = opts.get("page_range")
    if pr is not None:
        try:
            start, end = int(pr[0]), int(pr[1])
            if start < 1 or end < start:
                pr = None
            else:
                pr = [start, end]
        except (TypeError, ValueError, IndexError):
            pr = None
    opts["page_range"] = pr
    return opts


def _build_converter(preset: str, ocr: bool, tables: bool, images: str):
    """
    Construct a docling DocumentConverter for the given preset. Imports are
    deferred so the rest of the server (TTS, chat) can boot even without
    docling installed.
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    # Defaults vary across docling versions — set explicitly.
    pipeline_options.do_ocr = bool(ocr) or preset == "accurate"
    pipeline_options.do_table_structure = bool(tables) and preset != "fast"

    # Picture handling: docling exposes a few flags; not all exist across
    # versions, so set defensively.
    for attr, value in (
        ("generate_picture_images", images == "embed"),
        ("do_picture_classification", images == "describe"),
        ("do_picture_description", images == "describe"),
    ):
        if hasattr(pipeline_options, attr):
            setattr(pipeline_options, attr, value)

    format_options = {
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
    }

    # VLM pipeline (Accurate preset). Only attempt if the installed docling
    # exposes the VLM module — older releases don't.
    if preset == "accurate":
        try:
            from docling.datamodel.pipeline_options import VlmPipelineOptions  # type: ignore
            from docling.pipeline.vlm_pipeline import VlmPipeline  # type: ignore

            vlm_options = VlmPipelineOptions()
            format_options[InputFormat.PDF] = PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=vlm_options,
            )
        except Exception as e:
            logger.info("VLM pipeline unavailable; falling back to default for 'accurate'. (%s)", e)

    return DocumentConverter(format_options=format_options)


async def _get_converter(preset: str, ocr: bool, tables: bool, images: str):
    key = f"{preset}|{int(ocr)}|{int(tables)}|{images}"
    async with _converter_lock:
        if key not in _converters:
            logger.info("Building docling converter for preset=%s ocr=%s tables=%s images=%s",
                        preset, ocr, tables, images)
            _converters[key] = await asyncio.to_thread(
                _build_converter, preset, ocr, tables, images
            )
        return _converters[key]


def _split_markdown_by_pages(md: str) -> list[tuple[int, str]]:
    """
    Fallback: docling sometimes emits page boundaries as HTML comments
    (e.g. ``<!-- page break -->`` or ``<!-- page X -->``). If no markers are
    present, return one entry with page=1.
    """
    if not md:
        return [(1, "")]
    # Try numbered markers first.
    parts = re.split(r"<!--\s*page\s+(\d+)\s*-->", md, flags=re.IGNORECASE)
    if len(parts) >= 3:
        out: list[tuple[int, str]] = []
        # parts = [pre, '1', content1, '2', content2, ...]
        if parts[0].strip():
            out.append((1, parts[0].strip()))
        for i in range(1, len(parts), 2):
            try:
                page = int(parts[i])
            except ValueError:
                continue
            content = parts[i + 1] if i + 1 < len(parts) else ""
            out.append((page, content.strip()))
        return out or [(1, md.strip())]

    # Try generic page-break comments — split sequentially.
    pieces = re.split(r"<!--\s*(?:page[- ]?break|page\s+\d*)\s*-->", md, flags=re.IGNORECASE)
    if len(pieces) > 1:
        return [(i + 1, p.strip()) for i, p in enumerate(pieces) if p.strip()]

    return [(1, md.strip())]


def _doc_to_pages(doc) -> list[tuple[int, str]]:
    """
    Try several strategies to get per-page markdown out of a DoclingDocument.
    Returns ``[(page_no, markdown), ...]`` sorted by page.
    """
    # 1. Preferred: per-page export when available.
    pages_attr = getattr(doc, "pages", None)
    if pages_attr:
        try:
            page_nos = sorted(int(p) for p in pages_attr.keys())
        except Exception:
            page_nos = []
        if page_nos:
            out: list[tuple[int, str]] = []
            export = getattr(doc, "export_to_markdown", None)
            for n in page_nos:
                md = ""
                if callable(export):
                    for kwarg in ("page_no", "page"):
                        try:
                            md = export(**{kwarg: n})
                            break
                        except TypeError:
                            continue
                        except Exception as e:
                            logger.debug("export_to_markdown(%s=%d) failed: %s", kwarg, n, e)
                if isinstance(md, str) and md.strip():
                    out.append((n, md.strip()))
            if out:
                return out

    # 2. Fallback: export full document and split on page-boundary comments.
    full = ""
    try:
        full = doc.export_to_markdown()
    except Exception as e:
        logger.warning("export_to_markdown() failed: %s", e)
    return _split_markdown_by_pages(full)


def _convert_sync(pdf_path: Path, options: dict, converter) -> list[tuple[int, str]]:
    page_range = options.get("page_range")
    convert_kwargs = {}
    if page_range:
        # docling 2.x accepts page_range=(start, end) on convert(). If the
        # installed version doesn't, we just convert the whole doc.
        convert_kwargs["page_range"] = (page_range[0], page_range[1])

    try:
        result = converter.convert(source=str(pdf_path), **convert_kwargs)
    except TypeError:
        # page_range not supported on this docling version.
        result = converter.convert(source=str(pdf_path))

    document = getattr(result, "document", None)
    if document is None:
        raise RuntimeError("docling returned no document")

    pages = _doc_to_pages(document)
    if page_range:
        start, end = page_range
        pages = [(p, md) for (p, md) in pages if start <= p <= end]
    return pages


async def convert_pdf_to_markdown_pages(
    pdf_path: Path,
    options: dict | None = None,
) -> list[tuple[int, str]]:
    """
    Convert a PDF to per-page Markdown. Returns ``[(page_no, markdown), ...]``
    sorted by page. Raises if docling is unavailable or conversion fails.
    """
    if not DOCLING_ENABLED:
        raise RuntimeError("Docling is disabled. Set DOCLING_ENABLED=true to enable.")

    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found at {pdf_path}")

    opts = _normalize_options(options)
    converter = await _get_converter(opts["preset"], opts["ocr"], opts["tables"], opts["images"])
    return await asyncio.to_thread(_convert_sync, pdf_path, opts, converter)
