#!/usr/bin/env python3
"""Time a project-wide search (doc_search.search_scope) on synthetic data (C2.1 Task 7, spec 5.5).

Seeds N documents of M chunks each (random 768-d vectors, Zipf-distributed pseudo-words) into a
SCRATCH database, runs searches through the real function, prints p50/p95 in milliseconds, and
deletes what it seeded. Target: p95 under 300 ms on the CPU dev machine for 50 documents x 200
chunks (about 10,000 chunks). The query embedding is random and made locally: the time of the
embedding model itself is NOT included, only the database work.

    createdb ... natural_reader_bench     # a scratch database on a Postgres with pgvector
    python scripts/bench_project_search.py \\
        --database-url postgresql://natural_reader:natural_reader@127.0.0.1:5434/natural_reader_bench

The script applies the migrations to that database (they are idempotent) and refuses any database
named natural_reader. It never echoes the URL (it holds a password).

Six query shapes alternate, so the headline covers the cheap and the expensive paths:
  exact          three words one chunk really has: the all-words list finds it
  partial        the same three words plus one nobody has: no chunk has all four, so the
                 half-the-words list runs (spec 5.5, point 3)
  partial-common two of the most common words plus one nobody has: many candidate chunks
  long-mixed     23 words: 8 of the most common, 14 mid-frequency and rare, and one nobody has (a
                 pasted question: no chunk has them all, so the partial list always runs)
  long-common    32 words, the 31 most common plus one nobody has: the partial list's worst case
                 (cost once grew with the number of words; ts_rank_cd alone took 460 ms here)
  meaning        no text: the meaning list alone
Every shape must stay under the target at p95 (the exit status says so); with about 8 searches
per shape in the default 50, a shape's p95 is its slowest search.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REAL_DATABASE = "natural_reader"
PREFIX = "bench-"          # every document this script seeds starts with it, so cleanup is exact
SHAPES = ("exact", "partial", "partial-common", "long-mixed", "long-common", "meaning")
ABSENT = "zzqxv"           # a word no seeded chunk contains
TARGET_MS = 300            # spec 5.5: one search, p95


def check_database_url(url: str | None) -> None:
    """Exit unless `url` names a scratch database. Never echoes the URL."""
    from psycopg.conninfo import conninfo_to_dict

    def refuse(why: str) -> None:
        print(f"Refusing to run: {why}", file=sys.stderr)
        raise SystemExit(2)

    if not url:
        refuse("--database-url is required.")
    try:
        name = conninfo_to_dict(url).get("dbname")
    except Exception:
        refuse("--database-url isn't a connection string I can read.")
    if not name or name == REAL_DATABASE:
        refuse(f"--database-url names {'no database' if not name else f'the real database ({REAL_DATABASE})'}; "
               f"the benchmark writes thousands of rows. Use a scratch one, e.g. {REAL_DATABASE}_bench.")


def make_vocabulary(rng: random.Random, size: int) -> list[str]:
    """`size` distinct pronounceable pseudo-words, ordered so index 0 is the most common."""
    syllables = [a + b for a in "bcdfghklmnprstvz" for b in "aeiou"]
    words: set[str] = set()
    while len(words) < size:
        words.add("".join(rng.choice(syllables) for _ in range(rng.randint(2, 4))))
    ordered = sorted(words)               # a set's order changes between runs; the seed must decide everything
    rng.shuffle(ordered)
    return ordered


def percentile(sorted_ms: list[float], p: float) -> float:
    """Nearest-rank percentile of an ascending list."""
    return sorted_ms[min(len(sorted_ms) - 1, max(0, int(-(-p / 100 * len(sorted_ms) // 1)) - 1))]


def summarize(ms: list[float]) -> str:
    s = sorted(ms)
    return f"n={len(s):3d}  p50={statistics.median(s):7.1f}  p95={percentile(s, 95):7.1f}  max={s[-1]:7.1f}"


async def seed(conn, docs: int, chunks: int, dim: int, rng: random.Random, vocab: list[str],
               vacuum: bool = False) -> list[list[str]]:
    """Insert the documents and chunks; return each chunk's words (for building queries)."""
    weights = [1.0 / (i + 1) for i in range(len(vocab))]            # Zipf: a few words everywhere
    all_words: list[list[str]] = []
    for d in range(docs):
        doc_id = f"{PREFIX}{d:04d}"
        await conn.execute(
            "INSERT INTO documents (doc_id, file_name, file_type, size_bytes, state, extracted_by) "
            "VALUES (%s, %s, 'md', 1, 'indexed', 'client')", (doc_id, f"{doc_id}.md"))
        async with conn.cursor() as cur:
            async with cur.copy(
                    "COPY doc_chunks (doc_id, ord, page, chunk_type, text, text_hash, embedding, embedding_model) "
                    "FROM STDIN") as copy:
                for i in range(chunks):
                    words = rng.choices(vocab, weights=weights, k=90)
                    all_words.append(words)
                    vec = "[" + ",".join(f"{rng.gauss(0, 1):.4f}" for _ in range(dim)) + "]"
                    await copy.write_row((doc_id, i, 1 + i // 4, "page", " ".join(words), f"h{i}", vec, "bench"))
    await conn.commit()
    # A real library has statistics (without them the plan is a guess), and by the time a project is searched,
    # usually also a vacuum. A table that was only just bulk-loaded has neither the visibility map nor a clean
    # GIN pending list, which makes every search slower: the default here is that worst case (ANALYZE only);
    # --vacuum measures the steady state.
    await conn.set_autocommit(True)
    await conn.execute("VACUUM (ANALYZE) doc_chunks" if vacuum else "ANALYZE doc_chunks")
    await conn.set_autocommit(False)
    return all_words


def make_query(shape: str, rng: random.Random, vocab: list[str], all_words: list[list[str]], dim: int):
    qvec = [rng.gauss(0, 1) for _ in range(dim)]
    if shape == "meaning":
        return qvec, None
    if shape == "partial-common":
        return qvec, f"{vocab[0]} {vocab[1]} {ABSENT}"
    if shape == "long-mixed":
        words = vocab[:8] + rng.sample(vocab[100:], 14) + [ABSENT]
        rng.shuffle(words)
        return qvec, " ".join(words)
    if shape == "long-common":
        return qvec, " ".join(vocab[:31]) + f" {ABSENT}"
    mid = [w for w in rng.choice(all_words) if w not in vocab[:30]]          # not the very common ones
    three = " ".join(dict.fromkeys(mid[:3]))
    return qvec, three if shape == "exact" else f"{three} {ABSENT}"


async def run(url: str, docs: int, chunks: int, searches: int, k: int, per_doc: int, depth: int,
              dim: int, seed_: int, vacuum: bool = False, other_docs: int = 0) -> int:
    from psycopg import AsyncConnection
    from server.services.doc_search import search_scope
    from server.tests.dbutil import apply_migrations

    await apply_migrations(url)
    rng = random.Random(seed_)
    vocab = make_vocabulary(rng, 3000)
    conn = await AsyncConnection.connect(url)
    try:
        await conn.execute("DELETE FROM documents WHERE doc_id LIKE %s", (PREFIX + "%",))   # a crashed earlier run
        await conn.commit()
        started = time.perf_counter()
        all_words = await seed(conn, docs + other_docs, chunks, dim, rng, vocab, vacuum)
        print(f"seeded {docs + other_docs} documents x {chunks} chunks = {(docs + other_docs) * chunks} chunks "
              f"in {time.perf_counter() - started:.1f}s ({docs} of them are the searched project)", file=sys.stderr)
        scope = [f"{PREFIX}{d:04d}" for d in range(docs)]
        for shape in SHAPES:                                                 # untimed: caches and plans warm
            qvec, text = make_query(shape, rng, vocab, all_words, dim)
            await search_scope(conn, scope, qvec, k, text, per_doc=per_doc, depth=depth)
        timings: dict[str, list[float]] = {s: [] for s in SHAPES}
        found: dict[str, int] = {s: 0 for s in SHAPES}
        for i in range(searches):
            shape = SHAPES[i % len(SHAPES)]
            qvec, text = make_query(shape, rng, vocab, all_words, dim)
            t0 = time.perf_counter()
            rows = await search_scope(conn, scope, qvec, k, text, per_doc=per_doc, depth=depth)
            timings[shape].append((time.perf_counter() - t0) * 1000)
            if rows and any(r.get("by_words") for r in rows):
                found[shape] += 1
            assert len(rows) == k, f"{shape}: got {len(rows)} rows, wanted {k}"
        every = [ms for v in timings.values() for ms in v]
        print(f"Project search timing: {docs} documents x {chunks} chunks ({docs * chunks} chunks), "
              f"k={k}, per_doc={per_doc}, depth={depth}, {searches} searches, "
              f"{'vacuumed' if vacuum else 'freshly loaded, not vacuumed'}"
              f"{f', {other_docs} more documents outside the project' if other_docs else ''} (milliseconds)")
        print(f"  all             {summarize(every)}")
        for shape in SHAPES:
            print(f"  {shape:<15} {summarize(timings[shape])}  searches with a word match: {found[shape]}")
        worst = max(percentile(sorted(v), 95) for v in timings.values() if v)
        p95 = percentile(sorted(every), 95)
        ok = p95 < TARGET_MS and worst < TARGET_MS
        print(f"p95 {p95:.1f} ms overall, {worst:.1f} ms for the slowest shape, against a target of "
              f"{TARGET_MS} ms: {'OK' if ok else 'OVER TARGET'}")
        return 0 if ok else 1
    finally:
        await conn.rollback()
        cur = await conn.execute("DELETE FROM documents WHERE doc_id LIKE %s", (PREFIX + "%",))
        await conn.commit()
        print(f"removed {cur.rowcount} seeded documents (their chunks go with them)", file=sys.stderr)
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", required=True, help="a SCRATCH database (never natural_reader)")
    parser.add_argument("--docs", type=int, default=50)
    parser.add_argument("--chunks", type=int, default=200, help="chunks per document")
    parser.add_argument("--other-docs", type=int, default=0,
                        help="more documents in the library but outside the searched project (a bigger library)")
    parser.add_argument("--searches", type=int, default=50)
    parser.add_argument("--k", type=int, default=10, help="passages wanted (the chat tool allows up to 10)")
    parser.add_argument("--per-doc", type=int, default=2, help="CHAT_SEARCH_PER_DOC")
    parser.add_argument("--depth", type=int, default=50, help="CHAT_PROJECT_FUSION_DEPTH")
    parser.add_argument("--dim", type=int, default=768, help="vector size; the schema is 768")
    parser.add_argument("--vacuum", action="store_true",
                        help="VACUUM ANALYZE after loading (steady state) instead of ANALYZE only (just loaded)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    check_database_url(args.database_url)
    if args.docs < 1 or args.chunks < 1 or args.searches < 1:
        parser.error("--docs, --chunks and --searches must be at least 1")
    return asyncio.run(run(args.database_url, args.docs, args.chunks, args.searches, args.k, args.per_doc,
                           args.depth, args.dim, args.seed, args.vacuum, args.other_docs))


if __name__ == "__main__":
    sys.exit(main())
