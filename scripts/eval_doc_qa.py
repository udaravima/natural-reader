#!/usr/bin/env python3
"""Ask a chat model questions about a document with planted facts, through
the real chat path, and score the answers (v2.3 Task G).

Needs what the backend needs: DATABASE_URL, the embedding model (Ollama) and
the chat model's provider, configured by the same environment / .env file.

    python scripts/eval_doc_qa.py --model ollama:llama3.2:3b
    python scripts/eval_doc_qa.py --model openrouter:qwen/qwen3-8b --show-answers

Exit status: 0 when every case passed, 1 when the model failed a case, 2 when
the harness couldn't run (database, embedding model, provider). See
server/evals/doc_qa.py.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, help="a model id as the chat picker shows it, e.g. ollama:llama3.2:3b")
    parser.add_argument("--show-answers", action="store_true", help="print each answer as well as its score")
    parser.add_argument("--think", choices=("off", "on"), default="off",
                        help="the model's thinking (default off, as the chat sends it unless a user turns it on)")
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 11), metavar="N",
                        help="ask every case N times (1-10) and print a per-case tally")
    parser.add_argument("--prefetch", choices=("default", "auto", "on", "off"), default="default",
                        help="search the document before the model runs (CHAT_PREFETCH) for this run")
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if args.prefetch != "default":
        os.environ["CHAT_PREFETCH"] = args.prefetch   # after .env: the flag wins for this run
    # Only now: server modules read settings (chunk size, embedding input
    # limit) when they're imported, so .env has to be loaded first.
    from server.evals.doc_qa import EvalSetupError, format_report, run_eval
    try:
        runs = asyncio.run(run_eval(args.model, show_answers=args.show_answers, think=args.think,
                                    repeat=args.repeat))
    except EvalSetupError as e:
        print(f"Could not run the eval: {e}", file=sys.stderr)
        return 2
    print(format_report(runs, model=args.model))
    every = [r for results in runs for r in results]
    if any(r.error for r in every):
        return 2
    return 0 if all(r.passed for r in every if not r.skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
