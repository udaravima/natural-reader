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
    images:     'drop' | 'embed' | 'describe'       (default 'drop'; embed keeps each
                picture as a file beside the document, describe also captions it)
    page_range: [start, end]   1-based inclusive    (None = whole doc)

If the installed docling version doesn't expose a particular knob (page_range,
per-page export, VLM pipeline, etc.) we degrade gracefully rather than 500.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, NamedTuple

logger = logging.getLogger(__name__)

DOCLING_ENABLED = os.environ.get("DOCLING_ENABLED", "false").lower() in ("1", "true", "yes")

# Pictures a conversion keeps. Rendered at twice the page's 72 dpi: at 1 a figure
# comes out blurred. Under MIN_PICTURE_POINTS on a side (about 1 cm) it is a logo
# or a rule, not a figure: measured on a real PDF, a 91x16 footer on every page.
PICTURE_SCALE = 2.0
MIN_PICTURE_POINTS = 32
DEFAULT_MAX_PICTURES = 500
_FIGURE_REF = re.compile(r"(?:\n\n)?!\[[^\]]*\]\(figures/\d+\.png\)")


class Conversion(NamedTuple):
    pages: list[tuple[int, str]]   # (page_no, markdown), sorted by page
    pictures: list[bytes]          # PNGs; pictures[n - 1] is what figures/<n>.png in the Markdown names


def max_pictures() -> int:
    """DOCLING_MAX_PICTURES: pictures one document may keep. A figure past it stays
    a placeholder. It counts files, not bytes, so it bounds how many pictures a document keeps, not
    how much space they take."""
    raw = (os.environ.get("DOCLING_MAX_PICTURES") or "").strip()
    if raw.isdigit():
        return int(raw)
    if raw:
        logger.warning("DOCLING_MAX_PICTURES=%r isn't a whole number: using %d", raw, DEFAULT_MAX_PICTURES)
    return DEFAULT_MAX_PICTURES


def figure_ref(number: int) -> Path:
    """What the Markdown calls picture `number`. The reader turns it into the route."""
    return Path(f"figures/{number}.png")


def strip_figure_refs(markdown: str) -> str:
    """The page without its picture references: a path isn't words, so it stays out
    of the chunks, the embeddings and what the chat reads."""
    return _FIGURE_REF.sub("", markdown)


def collect_pictures(document) -> list[bytes]:
    """Each kept picture as PNG bytes, and point its Markdown reference at them.
    Docling exports a picture whose image is still a data: URI as a placeholder, so
    a tiny picture, or one past the cap, simply stays one. The same crop twice (a
    logo on every page) is one file."""
    cap = max_pictures()
    pictures: list[bytes] = []
    numbers: dict[bytes, int] = {}
    for picture in getattr(document, "pictures", None) or []:
        box = picture.prov[0].bbox if picture.prov else None
        if picture.image is None or (box is not None and min(box.width, box.height) < MIN_PICTURE_POINTS):
            continue
        image = picture.get_image(document)   # before the URI changes: it reads the data: URI
        if image is None:
            continue
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        png = buffer.getvalue()
        number = numbers.get(png)
        if number is None:
            if len(pictures) >= cap:
                continue
            pictures.append(png)
            number = numbers[png] = len(pictures)
        picture.image.uri = figure_ref(number)
    return pictures

# Lazy module-level cache: building a DocumentConverter is expensive (downloads
# models). We keep one instance per pipeline preset.
_converters: dict[str, Any] = {}
_converter_lock = asyncio.Lock()


def is_enabled() -> bool:
    return DOCLING_ENABLED


def check_available() -> bool:
    """Startup check: DOCLING_ENABLED=true needs Docling installed.

    The container image installs Docling only when built with
    WITH_DOCLING=true (startup.sh matches it to .env). If the two disagree,
    say so once at startup instead of failing every conversion.
    """
    if not DOCLING_ENABLED:
        return True
    if importlib.util.find_spec("docling") is not None:
        return True
    logger.error(
        "DOCLING_ENABLED=true but Docling is not installed in this environment: "
        "conversions will fail with 503. Container: run ./startup.sh rebuild "
        "(it builds with Docling when .env enables it). Venv: pip install -r "
        "requirements-docling.txt"
    )
    return False


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
    keep_pictures = images in ("embed", "describe")

    # Picture handling: docling exposes a few flags; not all exist across
    # versions, so set defensively.
    for attr, value in (
        ("generate_picture_images", keep_pictures),
        ("do_picture_classification", images == "describe"),
        ("do_picture_description", images == "describe"),
    ):
        if hasattr(pipeline_options, attr):
            setattr(pipeline_options, attr, value)
    if keep_pictures:
        pipeline_options.images_scale = PICTURE_SCALE

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
            if keep_pictures:
                vlm_options.generate_picture_images = True
                vlm_options.images_scale = PICTURE_SCALE
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


def _doc_to_pages(doc, **export_options) -> list[tuple[int, str]]:
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
                            md = export(**{kwarg: n}, **export_options)
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
        full = doc.export_to_markdown(**export_options)
    except Exception as e:
        logger.warning("export_to_markdown() failed: %s", e)
    return _split_markdown_by_pages(full)


def _value(x: Any) -> Any:
    return getattr(x, "value", x)   # docling's kinds and phases are str enums


def progress_from_event(event: Any) -> dict[str, Any] | None:
    """What the Convert button shows for one of docling's progress events
    (docling.datamodel.progress, docling 2.142+): the phase, and for pages and
    enrichment items how many of how many are done. Pages may finish out of
    order, so the count is `completed_pages`, not the page number. None for an
    event with nothing to show (a document starting or finishing)."""
    kind = _value(getattr(event, "kind", None))
    if kind == "phase_started":
        return {"phase": str(_value(event.phase))}
    if kind == "page_completed":
        return {"phase": "build", "done": event.completed_pages, "total": event.total_pages}
    if kind == "enrichment_progress":
        return {"phase": "enrich", "done": event.completed_items, "total": event.total_items,
                "label": str(event.label)}
    return None


def _convert_sync(pdf_path: Path, options: dict, converter,
                  on_progress: Callable[[dict], None] | None = None) -> Conversion:
    page_range = options.get("page_range")
    convert_kwargs = {}
    if on_progress is not None:
        def report(event):   # docling logs (and survives) a callback that raises
            progress = progress_from_event(event)
            if progress is not None:
                on_progress(progress)
        convert_kwargs["progress_callback"] = report
    if page_range:
        # docling 2.x accepts page_range=(start, end) on convert(). If the
        # installed version doesn't, we just convert the whole doc.
        convert_kwargs["page_range"] = (page_range[0], page_range[1])

    try:
        result = converter.convert(source=str(pdf_path), **convert_kwargs)
    except TypeError:
        # page_range (or progress_callback, before docling 2.142) not supported on this docling version.
        result = converter.convert(source=str(pdf_path))

    document = getattr(result, "document", None)
    if document is None:
        raise RuntimeError("docling returned no document")

    pictures: list[bytes] = []
    export_options: dict[str, Any] = {}
    if options.get("images") in ("embed", "describe"):
        from docling_core.types.doc.base import ImageRefMode  # type: ignore
        pictures = collect_pictures(document)
        export_options["image_mode"] = ImageRefMode.REFERENCED

    pages = _doc_to_pages(document, **export_options)
    if page_range:
        start, end = page_range
        pages = [(p, md) for (p, md) in pages if start <= p <= end]
    return Conversion(pages, pictures)


async def convert_pdf_to_markdown_pages(
    pdf_path: Path,
    options: dict | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> Conversion:
    """
    Convert a PDF to per-page Markdown, plus the pictures it keeps. Raises if
    docling is unavailable or conversion fails.
    `on_progress` gets each `progress_from_event` dict, called from docling's
    worker thread, not the event loop.
    """
    if not DOCLING_ENABLED:
        raise RuntimeError("Docling is disabled. Set DOCLING_ENABLED=true to enable.")

    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found at {pdf_path}")

    opts = _normalize_options(options)
    converter = await _get_converter(opts["preset"], opts["ocr"], opts["tables"], opts["images"])
    return await asyncio.to_thread(_convert_sync, pdf_path, opts, converter, on_progress)
