"""Run what the routes queued, as each worker's runners would: both lanes, until neither has work."""
from server.services import doc_pipeline

WORKER = "test-drain"


async def drain() -> list[str]:
    doc_pipeline._rebuilds_in_a_row.pop(WORKER, None)
    ran = []
    while True:
        step = [await doc_pipeline.run_queue_once(WORKER), await doc_pipeline.run_convert_once(WORKER)]
        if not any(step):
            return ran
        ran += [doc_id for doc_id in step if doc_id]
