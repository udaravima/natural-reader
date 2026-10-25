#!/usr/bin/env python3
"""Ask a chat model questions about a project of documents (a frozen copy of this
repository's own docs, optionally with 41 padding documents), through the real chat
path scoped to the project, and score the answers (C2.1 Task 4).

Needs what the backend needs: the embedding model (Ollama) and the chat model's
provider, configured by the same environment / .env file, and a SCRATCH database in
DATABASE_URL. The eval creates its own user, project and chats there; it refuses to
run against the database named natural_reader. An exported DATABASE_URL wins over .env.
The documents' files go to a temporary storage folder of their own (DOC_STORAGE_DIR is set to it
for this run, whatever .env or the environment say, and it is removed afterwards), never the
app's real one; --storage-dir PATH names another folder instead, which is kept.

    DATABASE_URL=postgresql://u:p@127.0.0.1:5434/natural_reader_eval \\
        python scripts/eval_project_qa.py --model ollama:gemma4:31b-cloud --variant 9

--web-fixture adds the web-read case and serves the web from files (server/evals/fixtures/web), in this process.

Exit status: 0 when every case passed, 1 when the model failed a case, 2 when the
harness couldn't run (database, embedding model, provider). See server/evals/project_qa.py.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REAL_DATABASE = "natural_reader"


def check_database_url(url: str | None) -> None:
    """Exit unless `url` names a scratch database. The server falls back to the real database
    when DATABASE_URL is unset, so unset is refused too. Never echoes the URL: it holds a password."""
    from psycopg.conninfo import conninfo_to_dict

    def refuse(why: str) -> None:
        print(f"Refusing to run: {why}", file=sys.stderr)
        raise SystemExit(2)

    if not url:
        refuse("DATABASE_URL isn't set, so the server would use its default, the real database. "
               f"Export a scratch one, e.g. .../{REAL_DATABASE}_eval.")
    try:
        name = conninfo_to_dict(url).get("dbname")
    except Exception:
        refuse("DATABASE_URL isn't a connection string I can read.")
    if not name or name == REAL_DATABASE:
        refuse(f"DATABASE_URL names {'no database' if not name else f'the real database ({REAL_DATABASE})'}; "
               f"the eval writes users, projects and chats. Use a scratch one, e.g. {REAL_DATABASE}_eval.")


@contextlib.contextmanager
def eval_storage(path: str | None = None):
    """Point DOC_STORAGE_DIR at the eval's own folder for the length of the block: a fresh temporary
    one, removed at the end (on an error or Ctrl-C too), or `path`, created if needed and kept.
    Setting the environment variable (not reading .env) is what storage_dir() reads first, so this wins
    over a value from .env or already exported; PDF_STORAGE_DIR only counts when DOC_STORAGE_DIR is empty.
    Call it after load_dotenv, so .env can't fill the variable back in, and before the eval runs."""
    own = path is None
    folder = Path(tempfile.mkdtemp(prefix="nr-eval-storage-")) if own else Path(path).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    before = os.environ.get("DOC_STORAGE_DIR")
    os.environ["DOC_STORAGE_DIR"] = str(folder)
    try:
        yield folder
    finally:
        if before is None:
            os.environ.pop("DOC_STORAGE_DIR", None)
        else:
            os.environ["DOC_STORAGE_DIR"] = before
        if own:
            shutil.rmtree(folder, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", required=True, help="a model id as the chat picker shows it, e.g. ollama:gemma4:31b-cloud")
    parser.add_argument("--variant", choices=("9", "50"), default="9",
                        help="9 = the nine fixture documents; 50 = plus 41 padding documents (default 9)")
    parser.add_argument("--repeat", type=int, default=1, choices=range(1, 11), metavar="N",
                        help="ask every case N times (1-10) and print a per-kind tally")
    parser.add_argument("--think", choices=("off", "on"), default="off",
                        help="the model's thinking (default off, as the chat sends it unless a user turns it on)")
    parser.add_argument("--show-answers", action="store_true", help="print each answer as well as its score")
    parser.add_argument("--web-fixture", action="store_true",
                        help="serve web search and page fetches from server/evals/fixtures/web (no network) and "
                             "also ask the web-read case: the offer, the user's yes, the answer from a long page")
    parser.add_argument("--storage-dir", metavar="PATH",
                        help="keep the documents' files in PATH (default: a temporary folder, removed afterwards)")
    args = parser.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)   # a variable already exported (DATABASE_URL) wins over .env
    except ImportError:
        pass
    check_database_url(os.environ.get("DATABASE_URL"))
    # Only now: server modules read settings when they're imported, so .env has to be loaded first.
    from server.evals.project_qa import EvalSetupError, format_report, run_eval
    try:
        with eval_storage(args.storage_dir):
            runs = asyncio.run(run_eval(args.model, variant=args.variant, think=args.think, repeat=args.repeat,
                                        show_answers=args.show_answers, web_fixture=args.web_fixture))
    except EvalSetupError as e:
        print(f"Could not run the eval: {e}", file=sys.stderr)
        return 2
    print(format_report(runs, model=args.model, variant=args.variant))
    every = [r for results in runs for r in results]
    if any(r.error for r in every):
        return 2
    return 0 if all(r.passed for r in every if not r.skipped) else 1


if __name__ == "__main__":
    sys.exit(main())
