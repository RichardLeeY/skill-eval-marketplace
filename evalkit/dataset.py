"""Reading `dataset.jsonl`, for both phases.

One reader, because there used to be two. `run_eval.load_cases` and
`run_strands_eval.load` each parsed the dataset with their own `json.loads` per
line, so the two phases could disagree about what a dataset says -- and one of
them resolved `{"from": …}` fixtures while the other did not. A case that phase 1
accepted and phase 2 skipped is the worst version of that disagreement: the run
happens, is paid for, and is never scored.

**Multiple cases, in either layout.** A dataset holds as many cases as the skill
needs, written either one object per line (the JSONL convention the extension
promises) or pretty-printed across several lines. The latter is not an
indulgence: these datasets carry prose assertions and multi-kilobyte
`seed_files`, and a case that has to fit on one line is a case nobody re-reads
before editing. A strict line-oriented reader also fails *loudly but wrongly* on
a pretty-printed file -- `Expecting property name enclosed in double quotes: line
1 column 2`, which points at the first line of the file rather than at the fact
that the file is formatted differently than the parser expects.

So the objects are decoded as a stream (`raw_decode`), which accepts both. A
top-level JSON array is accepted too, for the same reason: it is what an author
gets from `json.dump(cases, …)` and rejecting it teaches nothing.

`//` comment lines are dropped -- the datasets use them for the design notes that
belong next to the cases rather than in a commit message. They are replaced by
blank lines rather than removed, so a parse error still reports the line number
the author sees in their editor.
"""

from __future__ import annotations

import json
import os
from typing import Any

_WS = " \t\r\n"


def _blank_comments(raw: str) -> str:
    """Comment lines, emptied but not deleted, so line numbers survive."""
    return "\n".join("" if line.strip().startswith("//") else line
                     for line in raw.split("\n"))


def _line_of(body: str, index: int) -> int:
    return body.count("\n", 0, max(index, 0)) + 1


def decode_objects(raw: str, path: str = "<dataset>") -> list[dict]:
    """Every JSON object in `raw`, whatever the line layout.

    Raises `SystemExit` on a malformed dataset rather than propagating a
    `JSONDecodeError`: both runners call this before any agent is invoked, and the
    only useful response to a broken dataset is a message naming the file and line
    and then stopping. A traceback here is noise -- there is no bug to debug in
    the reader.
    """
    body = _blank_comments(raw)
    decoder = json.JSONDecoder()
    out: list[dict] = []
    i = 0
    while True:
        while i < len(body) and body[i] in _WS:
            i += 1
        if i >= len(body):
            return out
        start = i
        try:
            value, i = decoder.raw_decode(body, i)
        except json.JSONDecodeError as e:
            raise SystemExit(
                f"{path}:{_line_of(body, start)}: this case does not parse as JSON — "
                f"{e.msg} (at line {_line_of(body, e.pos)}, column {e.colno})"
            ) from e
        # A top-level array is one author's `json.dump(cases)` away, and its
        # elements are cases exactly as a stream of objects would be.
        for item in (value if isinstance(value, list) else [value]):
            if not isinstance(item, dict):
                raise SystemExit(
                    f"{path}:{_line_of(body, start)}: expected a case object, "
                    f"got {type(item).__name__}")
            out.append(item)


def resolve_seeds(case: dict, dataset_dir: str) -> dict:
    """Expand `{"from": "<path>"}` seed values by reading the file.

    A seed value is normally the file's content inline, which keeps a case
    self-contained and readable. That stops working once the input is a parsed
    workbook: inlining tens of kilobytes of JSON three times over buries the case
    in its own fixture, and every edit to the fixture has to be made three times.
    The path is relative to the dataset, so a fixture stays a file that can be
    regenerated and diffed.

    Resolved here rather than in the sandbox because the sandbox may be a remote
    runtime with no access to this repo, and because a missing fixture should stop
    the suite before it pays for an agent run.
    """
    seeds = case.get("seed_files")
    if not isinstance(seeds, dict):
        return case
    out = {}
    for rel, val in seeds.items():
        if isinstance(val, dict) and "from" in val:
            src = os.path.normpath(os.path.join(dataset_dir, val["from"]))
            try:
                with open(src, encoding="utf-8") as fh:
                    val = fh.read()
            except OSError as e:
                raise SystemExit(
                    f"{case.get('id')}: seed_files[{rel!r}] reads {val['from']!r} "
                    f"relative to {dataset_dir} — {e}"
                ) from e
        out[rel] = val
    case["seed_files"] = out
    return case


def load_cases(path: str, only: list[str] | None = None,
               with_seeds: bool = True) -> list[dict[str, Any]]:
    """The cases one dataset declares, filtered to `only` if given.

    `with_seeds=False` for phase 2, which scores a recording and has no workspace
    to seed. Reading the fixtures there would only add a way for re-scoring a run
    to fail on a file the run no longer needs.
    """
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    cases = decode_objects(raw, path)
    for case in cases:
        missing = [k for k in ("id", "prompt") if not case.get(k)]
        if missing:
            raise SystemExit(f"{path}: a case is missing {', '.join(missing)}: "
                             f"{json.dumps(case, ensure_ascii=False)[:160]}")
    if only:
        cases = [c for c in cases if c["id"] in only]
    if with_seeds:
        dataset_dir = os.path.dirname(path)
        cases = [resolve_seeds(c, dataset_dir) for c in cases]
    return cases
