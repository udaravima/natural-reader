"""Docling's progress events, as the convert job stores them for the
Convert button (documents.conversion_progress). Duck-typed: the tests run
without Docling installed, as they do in a backend built without it."""
from enum import Enum
from types import SimpleNamespace as E

from server.services.docling_convert import progress_from_event


class Kind(str, Enum):   # docling's ConversionProgressKind is a str enum too
    PHASE = "phase_started"
    PAGE = "page_completed"
    ENRICH = "enrichment_progress"
    DONE = "document_completed"


class Phase(str, Enum):
    INITIALIZE = "initialize"
    BUILD = "build"


def test_page_completed_counts_pages_done_not_page_numbers():
    """Pages may finish out of order: completed_pages drives the count."""
    assert progress_from_event(E(kind=Kind.PAGE, page_no=7, completed_pages=3, total_pages=40)) == {
        "phase": "build", "done": 3, "total": 40}


def test_a_phase_has_no_count_until_its_items_report():
    assert progress_from_event(E(kind=Kind.PHASE, phase=Phase.INITIALIZE)) == {"phase": "initialize"}
    assert progress_from_event(E(kind="phase_started", phase="assemble")) == {"phase": "assemble"}


def test_enrichment_carries_its_label():
    assert progress_from_event(E(kind=Kind.ENRICH, step="picture_description", label="picture description",
                                 completed_items=2, total_items=5)) == {
        "phase": "enrich", "done": 2, "total": 5, "label": "picture description"}


def test_other_events_store_nothing():
    assert progress_from_event(E(kind=Kind.DONE, status="success")) is None
    assert progress_from_event(E(kind="document_started")) is None
    assert progress_from_event(object()) is None
