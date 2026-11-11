"""One batch can't hold read-aloud for hours: its total length is capped, not only each sentence."""
import pytest
from pydantic import ValidationError

from server import schemas


def test_a_batch_within_the_total_is_accepted():
    schemas.BatchTTSRequest(sentences=["a" * 5000] * (schemas.MAX_BATCH_CHARS // 5000))


def test_a_batch_over_the_total_is_refused_with_the_limit_in_the_message():
    with pytest.raises(ValidationError) as err:
        schemas.BatchTTSRequest(sentences=["a" * 5000] * (schemas.MAX_BATCH_CHARS // 5000 + 1))
    assert f"{schemas.MAX_BATCH_CHARS:,}" in str(err.value)


def test_the_default_total_fits_a_long_page_but_not_a_book():
    assert schemas.MAX_BATCH_CHARS == 200_000
    assert schemas.MAX_SENTENCES * schemas.MAX_SENTENCE_CHARS >= 50 * schemas.MAX_BATCH_CHARS   # what it used to allow
