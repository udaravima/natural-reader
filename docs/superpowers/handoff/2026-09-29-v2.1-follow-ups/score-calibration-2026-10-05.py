"""Measure cosine scores the app's search sees, with nomic-embed-text via
Ollama, on real documents chunked by the app's own chunker.
For each question: the best score of a chunk that holds the answer, its rank,
and the best score of a chunk that doesn't. For unrelated messages: the top
score of any chunk. Both with the v2.3 prefixes and without (pre-v2.3).

Needs Ollama with nomic-embed-text, and holmes.txt (Project Gutenberg #1661)
next to this file. Vectors are cached as .npy files beside it."""
import asyncio, json, sys
from pathlib import Path
import httpx, numpy as np

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from server.services import extract

S = Path(__file__).parent
OLLAMA = "http://127.0.0.1:11434"
DP, QP = "search_document: ", "search_query: "

HOLMES = [  # (question, needle in the answering chunk)
    ("What job did the Red-Headed League give Jabez Wilson?", "Encyclopædia Britannica"),
    ("Why does Holmes say Watson sees but does not observe?", "seventeen steps"),
    ("What killed Julia Stoner?", "swamp adder"),
    ("Who is Neville St. Clair really?", "Hugh Boone"),
    ("Who was Hosmer Angel?", "Windibank"),
    ("What did the five orange pips stand for?", "Ku Klux"),
    ("Who took the beryl coronet?", "Burnwell"),
    ("What colour dress was Violet Hunter asked to wear?", "electric blue"),
    ("How did the engineer lose his thumb?", "cleaver"),
    ("Who did Lord St. Simon's bride run away with?", "Moulton"),
    ("Which woman outwitted Holmes in the Bohemia scandal?", "Irene Adler"),
    ("Where was the blue carbuncle hidden?", "crop"),
]
GUIDE = [
    ("How do I create a personal access token?", "Type a name"),
    ("Why does the browser extension need a token?", "Chrome read-aloud extension"),
    ("Can I see my token again after creating it?", "stores only a"),
    ("What happens when I run out of daily tokens?", "send button disables"),
    ("When does the chat budget reset?", "UTC midnight"),
    ("A new colleague sees waiting for approval, why?", "pending"),
    ("I was made admin but there's no Admin button", "Reload the page"),
    ("How do I offboard a user properly?", "Disable"),
    ("Does stopping a reply early refund tokens?", "refund"),
]
OFF_TOPIC = [
    "thanks!", "hi", "What's the weather in Colombo today?", "Write me a haiku about cats",
    "What is 17 times 23?", "Explain quantum entanglement simply",
    "Translate good morning into Sinhala", "Can you explain that more simply?",
    "Summarize this document", "What did you mean by that?",
    "How do I install Python on Windows?", "Who won the 2018 World Cup?",
]


async def embed(client, texts):
    out = []
    for t in texts:
        r = await client.post(f"{OLLAMA}/api/embeddings", json={"model": "nomic-embed-text", "prompt": t})
        r.raise_for_status()
        v = np.array(r.json()["embedding"]); out.append(v / np.linalg.norm(v))
    return np.array(out)


def chunks_of(name):
    if name == "holmes":
        raw = (S / "holmes.txt").read_text()
        raw = raw[raw.index("I. A SCANDAL IN BOHEMIA"):raw.index("*** END OF")]
        ex = extract.extract_text(raw)
    else:
        ex = extract.extract_markdown(ROOT / "docs" / "USER_GUIDE.md".read_text())
    return [c.text for c in extract.split_chunks(ex.chunks)]


async def main():
    res = {}
    async with httpx.AsyncClient(timeout=120) as client:
        for name, qs in (("guide", GUIDE), ("holmes", HOLMES)):
            texts = chunks_of(name)
            for q, n in qs:
                assert any(n in t for t in texts), (name, n)
            for mode, dp, qp in (("prefixed", DP, QP), ("plain", "", ""), ("mixed", "", QP)):
                cache = S / f"vec-{name}-{mode if mode != 'mixed' else 'plain'}.npy"
                if cache.exists():
                    D = np.load(cache)
                else:
                    D = await embed(client, [dp + t for t in texts]); np.save(cache, D)
                Q = await embed(client, [qp + q for q, _ in qs])
                O = await embed(client, [qp + q for q in OFF_TOPIC])
                rows = []
                for (q, n), qv in zip(qs, Q):
                    s = D @ qv
                    rel = [i for i, t in enumerate(texts) if n in t]
                    order = list(np.argsort(-s))
                    best = max(rel, key=lambda i: s[i])
                    rows.append(dict(q=q, rel=float(s[best]), rank=order.index(best) + 1,
                                     irr=float(max(s[i] for i in range(len(texts)) if i not in rel))))
                off = [dict(q=q, top=float((D @ ov).max())) for q, ov in zip(OFF_TOPIC, O)]
                res[(name, mode)] = (len(texts), rows, off)
    for (name, mode), (n, rows, off) in res.items():
        print(f"\n== {name} ({n} chunks), {mode}")
        for r in rows:
            print(f"  rel {r['rel']:.3f} rank {r['rank']:>3}  best-wrong {r['irr']:.3f}  {r['q']}")
        rel = [r['rel'] for r in rows]; irr = [r['irr'] for r in rows]; top = [o['top'] for o in off]
        print(f"  answering chunk: min {min(rel):.3f} median {np.median(rel):.3f} max {max(rel):.3f}; "
              f"top-1 {sum(r['rank']==1 for r in rows)}/{len(rows)}, top-5 {sum(r['rank']<=5 for r in rows)}/{len(rows)}")
        print(f"  best wrong chunk: median {np.median(irr):.3f} max {max(irr):.3f}")
        print(f"  unrelated messages, top score: min {min(top):.3f} median {np.median(top):.3f} max {max(top):.3f}")
        for o in sorted(off, key=lambda o: -o['top'])[:4]:
            print(f"     {o['top']:.3f}  {o['q']}")
    json.dump({f"{k[0]}|{k[1]}": v for k, v in res.items()}, open(S / "calibration.json", "w"), indent=1)

asyncio.run(main())
