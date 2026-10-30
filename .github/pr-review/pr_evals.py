#!/usr/bin/env python3
"""Build and validate PR review examples from the original PR eval run.

The sheet `chainlink-coding-standards-ai-automation-poc.xlsx` and
`out/pr_evals.csv` in `intern4all/pr-eval` are the output of that repo's
`run.sh` + `make-csv.py`. This script replays those responses into the
inline-review format used by `.github/workflows/pr-coding-standards-review.yaml`.

Subcommands:
  collect   find merged PRs reviewed by a user and save metadata + diffs
  replay    convert eval markdown responses into example review payloads
  validate  prove example comments anchor to real diff lines
  run       run the live reviewer over a set in dry-run mode

Example:
  python3 .github/pr-review/pr_evals.py replay \
    --eval-dir ~/Projects/intern4all/pr-eval/out \
    --prs-json ~/Projects/intern4all/pr-eval/build/prs.json \
    --diff-dir ~/Projects/intern4all/pr-eval/build/prs \
    --out-dir .github/pr-review/examples \
    --prs 23837,23832,23878 \
    --with-jmank
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import review  # noqa: E402

BULLET_RE = re.compile(r"^[-*]\s+(\S+):(\d+)\s+-\s+(.*)$")
QUOTE_RE = re.compile(r'"([^"]{15,})"')
CATEGORY_RE = re.compile(
    r"\s+-\s+(Design|Correctness|Concurrency|Lifecycle|Errors|Performance|Tests|"
    r"Security|Naming|Comments|Refactoring|Complexity|Coupling)\s*:.*$",
    re.I,
)
SEVERITY_BY_CATEGORY = {
    "correctness": "high",
    "concurrency": "high",
    "lifecycle": "high",
    "errors": "high",
    "security": "high",
    "performance": "medium",
    "tests": "medium",
    "design": "medium",
    "complexity": "medium",
    "coupling": "medium",
    "refactoring": "medium",
    "naming": "low",
    "comments": "low",
}


def load_standards(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def infer_severity(text: str) -> str:
    lowered = text.lower()
    for category, severity in SEVERITY_BY_CATEGORY.items():
        if re.search(rf"\b{category}\b", lowered):
            return severity
    if re.search(
        r"\b(panic|data loss|deadlock|race|corrupt|security|outage|leak|"
        r"wrong|fails|breaks|unbounded|never)\b",
        lowered,
    ):
        return "high"
    if re.search(r"\b(error|retry|test|nil|missing)\b", lowered):
        return "medium"
    if re.search(r"\b(nit|naming|comment|style)\b", lowered):
        return "low"
    return "medium"


def parse_bullet(
    line: str, standards: str
) -> Optional[Dict[str, Any]]:
    match = BULLET_RE.match(line.strip())
    if not match:
        return None
    path, line_number, rest = match.group(1), int(match.group(2)), match.group(3)

    # The eval output sometimes names more than one file in the location, e.g.
    # `a.go:26 / b.go:27`. The regex above only matches single-file bullets.
    if not path.endswith((".go", ".sql", ".yaml", ".yml", ".md", ".json", ".toml")):
        return None

    quotes = QUOTE_RE.findall(rest)
    standard = max((quote for quote in quotes if quote in standards), key=len, default=None)
    if standard is None:
        # Fall back to the longest quote so the example still shows the shape,
        # but mark it so a human can fix the quote before using it.
        standard = max(quotes, key=len, default="")

    fix = ""
    body = rest
    if standard:
        marker = '"' + standard + '"'
        index = rest.find(marker)
        if index >= 0:
            body = rest[:index].rstrip(" -")
            fix = rest[index + len(marker) :].lstrip(" -")
    if " - recommended action: " in body:
        body, fix = body.split(" - recommended action: ", 1)
    if " - recommended action: " in fix:
        fix = fix.split(" - recommended action: ", 1)[1]
    fix = re.sub(r"^recommended action:\s*", "", fix, flags=re.I).strip()

    body = CATEGORY_RE.sub("", body).strip()
    if not fix:
        # Last resort: the last ` - ` separated clause is usually the action.
        chunks = [chunk.strip() for chunk in body.split(" - ") if chunk.strip()]
        if len(chunks) >= 2:
            fix = chunks[-1]
            body = " - ".join(chunks[:-1])
    if not body or not fix:
        return None

    severity = infer_severity(rest)
    category_match = re.search(
        r"\s+-\s+(Design|Correctness|Concurrency|Lifecycle|Errors|Performance|"
        r"Tests|Security|Naming|Comments|Refactoring|Complexity|Coupling)\s*:",
        rest,
        re.I,
    )
    title = category_match.group(1).title() if category_match else ""
    return {
        "path": path,
        "line": line_number,
        "severity": severity,
        "title": title,
        "body": body[:1500],
        "standard": standard[:1000],
        "fix": fix[:1000],
    }


def parse_eval_markdown(text: str, standards: str) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for line in text.splitlines():
        if not line.startswith(("- ", "* ")):
            continue
        parsed = parse_bullet(line, standards)
        if parsed:
            findings.append(parsed)
    return findings


def load_prs(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"expected a JSON array in {path}")
    return data


def jmank_comments(repo: str, pr: int) -> Tuple[List[Dict[str, Any]], str]:
    proc = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/pulls/{pr}/comments",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return [], f"could not fetch comments: {proc.stderr.strip()}\n"
    comments = json.loads(proc.stdout or "[]")
    mine = [c for c in comments if (c.get("user") or {}).get("login") == "jmank88"]
    lines = [f"# jmank88 inline comments on {repo}#{pr}", ""]
    for comment in mine:
        path = comment.get("path", "")
        line = comment.get("line") or comment.get("original_line") or ""
        body = (comment.get("body") or "").strip()
        lines.append(f"## `{path}:{line}`")
        lines.append("")
        lines.append(body)
        lines.append("")
    if not mine:
        lines.append("_No inline comments by jmank88 on this PR._")
        lines.append("")
    return mine, "\n".join(lines)


def cmd_replay(args: argparse.Namespace) -> int:
    eval_dir = Path(args.eval_dir).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    prs_json = Path(args.prs_json).expanduser().resolve()
    diff_dir = Path(args.diff_dir).expanduser().resolve()
    standards = load_standards(Path(args.standards))
    wanted = [int(value) for value in args.prs.split(",") if value.strip()]
    rows = {int(row["number"]): row for row in load_prs(prs_json)}

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: List[Dict[str, Any]] = []

    for number in wanted:
        response_path = eval_dir / f"{number}.md"
        diff_path = diff_dir / f"{number}.diff"
        if not response_path.exists() or not diff_path.exists():
            print(f"skip #{number}: missing {response_path} or {diff_path}", file=sys.stderr)
            continue
        row = rows.get(number, {})
        text = response_path.read_text(encoding="utf-8")
        findings = parse_eval_markdown(text, standards)
        if not findings:
            print(f"skip #{number}: no parseable findings", file=sys.stderr)
            continue

        meta = {
            "repo": args.repo,
            "pr": number,
            "title": row.get("title", ""),
            "url": row.get("url", ""),
            "base_sha": row.get("baseRefOid", ""),
            "head_sha": row.get("headRefOid", ""),
            "merged_at": row.get("mergedAt", ""),
            "source": "intern4all/pr-eval",
        }
        diff_text = diff_path.read_text(encoding="utf-8")
        normalized = review.normalize_output({"findings": findings, "general": []})
        payload = review.build_payload(
            normalized,
            review.parse_diff(diff_text),
            repo=args.repo,
            pr=number,
            head_sha=meta["head_sha"],
            base_sha=meta["base_sha"],
            min_severity="low",
            max_findings=args.max_findings,
            existing=[],
            standards_ref="develop",
        )

        directory = out_dir / str(number)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "eval.md").write_text(text, encoding="utf-8")
        (directory / "findings.json").write_text(
            json.dumps(normalized, indent=2) + "\n", encoding="utf-8"
        )
        (directory / "meta.json").write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8"
        )
        (directory / "diff.patch").write_text(diff_text, encoding="utf-8")
        review.write_outputs(directory, payload, meta)
        if args.with_jmank:
            _, markdown = jmank_comments(args.repo, number)
            (directory / "jmank.md").write_text(markdown, encoding="utf-8")

        inline = len(payload.get("comments", [])) if payload else 0
        general = payload.get("body", "").count("- `") if payload else 0
        manifest.append(
            {
                "pr": number,
                "title": meta["title"],
                "findings": len(findings),
                "inline": inline,
                "general": general,
            }
        )
        print(f"#{number}: {len(findings)} findings -> {inline} inline")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(manifest)} example(s) to {out_dir}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    examples = Path(args.examples_dir).expanduser().resolve()
    diff_dir = Path(args.diff_dir).expanduser().resolve() if args.diff_dir else None
    failures = 0
    checked = 0
    for directory in sorted(examples.iterdir()) if examples.exists() else []:
        if not directory.is_dir() or not directory.name.isdigit():
            continue
        payload_path = directory / "review.json"
        meta_path = directory / "meta.json"
        if not payload_path.exists() or not meta_path.exists():
            continue
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        if payload is None:
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        diff_path = directory / "diff.patch"
        if not diff_path.exists() and diff_dir:
            diff_path = diff_dir / f"{directory.name}.diff"
        if diff_path.exists():
            diff_text = diff_path.read_text(encoding="utf-8")
        elif args.online:
            proc = subprocess.run(
                ["gh", "pr", "diff", directory.name, "-R", meta.get("repo", args.repo)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                print(f"{directory.name}: could not fetch diff: {proc.stderr.strip()}")
                failures += 1
                continue
            diff_text = proc.stdout
        else:
            print(f"{directory.name}: no cached diff; use --online or --diff-dir")
            failures += 1
            continue

        diff = review.parse_diff(diff_text)
        errors: List[str] = []
        for comment in payload.get("comments", []):
            path = comment.get("path")
            line = comment.get("line")
            side = comment.get("side", "RIGHT")
            if path not in diff:
                errors.append(f"{path}: not in diff")
            elif not isinstance(line, int) or review.anchor(diff, path, line, side) != (line, side):
                errors.append(f"{path}:{line} ({side}) is not a valid anchor")
        checked += 1
        if errors:
            failures += 1
            print(f"{directory.name}: INVALID")
            for error in errors:
                print(f"  - {error}")
        else:
            print(f"{directory.name}: ok ({len(payload.get('comments', []))} inline)")
    if not checked:
        print("no examples found", file=sys.stderr)
        return 1
    print(f"checked {checked} example(s), {failures} failure(s)")
    return 1 if failures else 0


def cmd_collect(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    diff_dir = out_dir / "diffs"
    diff_dir.mkdir(exist_ok=True)

    raw = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "-R",
            args.repo,
            "--state",
            "merged",
            "--search",
            f"reviewed-by:{args.reviewer} sort:updated-desc",
            "--limit",
            str(args.candidates),
            "--json",
            "number,title,url,mergedAt,headRefOid,baseRefOid,baseRefName",
        ],
        capture_output=True,
        text=True,
    )
    if raw.returncode != 0:
        print(raw.stderr, file=sys.stderr)
        return 1
    candidates = json.loads(raw.stdout or "[]")
    candidates.sort(key=lambda row: row.get("mergedAt", ""), reverse=True)

    selected: List[Dict[str, Any]] = []
    for row in candidates:
        title = row.get("title", "")
        if re.search(r"\b(bump|lint)\b", title, re.I):
            continue
        if row.get("baseRefName") != args.base:
            continue
        files = subprocess.run(
            ["gh", "pr", "diff", str(row["number"]), "-R", args.repo, "--name-only"],
            capture_output=True,
            text=True,
        )
        if files.returncode != 0 or not re.search(r"\.go$", files.stdout, re.M):
            continue
        selected.append(row)
        if len(selected) >= args.count:
            break

    if len(selected) < args.count:
        print(
            f"only {len(selected)} qualifying PRs found (wanted {args.count})",
            file=sys.stderr,
        )
        return 1

    for row in selected:
        diff = subprocess.run(
            ["gh", "pr", "diff", str(row["number"]), "-R", args.repo],
            capture_output=True,
            text=True,
        )
        if diff.returncode != 0:
            print(f"could not fetch #{row['number']}: {diff.stderr}", file=sys.stderr)
            return 1
        (diff_dir / f"{row['number']}.diff").write_text(diff.stdout, encoding="utf-8")

    (out_dir / "prs.json").write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")
    print(f"saved {len(selected)} PRs to {out_dir / 'prs.json'}")
    for row in selected:
        print(f"  #{row['number']} {row['title']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    prs_path = Path(args.prs_json).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = load_prs(prs_path)
    records: List[Dict[str, Any]] = []

    for row in rows:
        number = int(row["number"])
        work_dir = out_dir / str(number)
        command = [
            sys.executable,
            str(HERE / "review.py"),
            "run",
            "--repo",
            args.repo,
            "--pr",
            str(number),
            "--workdir",
            args.workdir,
            "--out-dir",
            str(work_dir),
            "--min-severity",
            args.min_severity,
            "--max-findings",
            str(args.max_findings),
            "--dry-run",
        ]
        if args.checkout:
            command.append("--checkout")
        proc = subprocess.run(command, text=True)
        if proc.returncode != 0:
            print(f"#{number}: review failed", file=sys.stderr)
            continue
        review_path = work_dir / "review.md"
        response = review_path.read_text(encoding="utf-8") if review_path.exists() else ""
        violation_count = response.count("### `")
        records.append(
            {
                "pr_number": number,
                "pr_url": row.get("url", ""),
                "title": row.get("title", ""),
                "merge_date": row.get("mergedAt", ""),
                "violation_count": violation_count,
                "glm-5-3-flash_response": response,
            }
        )

    jsonl_path = out_dir / "pr_evals.jsonl"
    csv_path = out_dir / "pr_evals.csv"
    with open(jsonl_path, "w", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    {
                        "merge_date": record["merge_date"],
                        "pr_url": record["pr_url"],
                        "glm-5-3-flash_response": record["glm-5-3-flash_response"],
                    }
                )
                + "\n"
            )
    if records:
        with open(csv_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
    print(f"wrote {jsonl_path} and {csv_path} ({len(records)} rows)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="find merged PRs reviewed by a user")
    collect.add_argument("--repo", default="smartcontractkit/chainlink")
    collect.add_argument("--reviewer", default="jmank88")
    collect.add_argument("--base", default="develop")
    collect.add_argument("--count", type=int, default=5)
    collect.add_argument("--candidates", type=int, default=60)
    collect.add_argument("--out-dir", default="/tmp/pr-review/evals")
    collect.set_defaults(func=cmd_collect)

    replay = sub.add_parser("replay", help="convert eval responses to review examples")
    replay.add_argument("--repo", default="smartcontractkit/chainlink")
    replay.add_argument("--eval-dir", required=True)
    replay.add_argument("--prs-json", required=True)
    replay.add_argument("--diff-dir", required=True)
    replay.add_argument("--out-dir", default=str(HERE / "examples"))
    replay.add_argument("--prs", required=True)
    replay.add_argument("--standards", default=str(HERE.parent.parent / ".github" / "copilot-instructions.md"))
    replay.add_argument("--max-findings", type=int, default=8)
    replay.add_argument("--with-jmank", action="store_true")
    replay.set_defaults(func=cmd_replay)

    validate = sub.add_parser("validate", help="validate example review payloads")
    validate.add_argument("--repo", default="smartcontractkit/chainlink")
    validate.add_argument("--examples-dir", default=str(HERE / "examples"))
    validate.add_argument("--diff-dir")
    validate.add_argument("--online", action="store_true")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="run the live reviewer over a set in dry-run")
    run.add_argument("--repo", default="smartcontractkit/chainlink")
    run.add_argument("--prs-json", required=True)
    run.add_argument("--workdir", default=".")
    run.add_argument("--out-dir", default="/tmp/pr-review/evals")
    run.add_argument("--min-severity", choices=["low", "medium", "high"], default="medium")
    run.add_argument("--max-findings", type=int, default=8)
    run.add_argument("--checkout", action="store_true")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
