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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.evals.doc_qa import EvalSetupError, format_report, run_eval  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, help="a model id as the chat picker shows it, e.g. ollama:llama3.2:3b")
    parser.add_argument("--show-answers", action="store_true", help="print each answer as well as its score")
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    try:
        results = asyncio.run(run_eval(args.model, show_answers=args.show_answers))
    except EvalSetupError as e:
        print(f"Could not run the eval: {e}", file=sys.stderr)
        return 2
    print(format_report(results, model=args.model))
    if any(r.error for r in results):
        return 2
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
