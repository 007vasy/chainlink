#!/usr/bin/env python3
"""Run the coding-standards reviewer and turn its findings into a GitHub review.

The reviewer is opencode with the `pr-reviewer` agent in `opencode.review.json`.
It receives the PR diff, the rulebook from the base commit, and a read-only
checkout of the PR head. It returns JSON findings; this script anchors every
finding to a real diff line and posts one COMMENT review with inline comments.

Subcommands:
  run       review a PR (dry-run by default with --dry-run)
  render    turn an existing findings JSON into a review payload
  validate  prove every inline comment anchors to a real diff line
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MARKER = "<!-- pr-coding-standards-review -->"
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}
SEVERITY_LABEL = {"low": "Low", "medium": "Medium", "high": "High"}
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LOCATION_RE = re.compile(r"^([^:]+):(\d+)$")

HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# process helpers
# ---------------------------------------------------------------------------


def run(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path | str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    check: bool = True,
    input_text: Optional[str] = None,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        list(cmd),
        cwd=str(cwd) if cwd else None,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-4000:]}"
        )
    return proc


def gh(*args: str, check: bool = True) -> str:
    return run(["gh", *args], check=check).stdout


def gh_json(*args: str, check: bool = True) -> Any:
    return json.loads(gh(*args, check=check) or "null")


def gh_raw(path: str) -> str:
    return run(
        ["gh", "api", "-H", "Accept: application/vnd.github.raw", path],
        check=True,
    ).stdout


def git(*args: str, cwd: Optional[Path | str] = None, check: bool = True) -> str:
    return run(["git", *args], cwd=cwd, check=check).stdout


# ---------------------------------------------------------------------------
# diff parsing and anchoring
# ---------------------------------------------------------------------------


@dataclass
class DiffFile:
    path: str
    added: set[int] = field(default_factory=set)
    context: set[int] = field(default_factory=set)
    deleted: set[int] = field(default_factory=set)
    right: set[int] = field(default_factory=set)
    left: set[int] = field(default_factory=set)


def parse_diff(diff_text: str) -> Dict[str, DiffFile]:
    """Map each file in a unified diff to the line numbers a review can target."""
    files: Dict[str, DiffFile] = {}
    cur: Optional[DiffFile] = None
    old_line = new_line = 0
    in_hunk = False

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            match = re.search(r" b/(.+)$", raw)
            path = match.group(1) if match else None
            if path:
                cur = files.setdefault(path, DiffFile(path))
            else:
                cur = None
            old_line = new_line = 0
            in_hunk = False
            continue

        if raw.startswith("+++ b/"):
            path = raw[6:]
            cur = files.setdefault(path, DiffFile(path))
            old_line = new_line = 0
            in_hunk = False
            continue

        if raw.startswith("--- a/"):
            continue

        if raw.startswith("@@"):
            match = HUNK_RE.match(raw)
            if match and cur:
                old_line = int(match.group(1))
                new_line = int(match.group(3))
                in_hunk = True
            continue

        if not cur or not in_hunk:
            continue

        if raw.startswith("+") and not raw.startswith("+++"):
            cur.added.add(new_line)
            cur.right.add(new_line)
            new_line += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            cur.deleted.add(old_line)
            cur.left.add(old_line)
            old_line += 1
        elif raw.startswith(" "):
            cur.context.add(new_line)
            cur.right.add(new_line)
            cur.left.add(old_line)
            old_line += 1
            new_line += 1
        elif raw.startswith("\\"):
            continue

    return files


def anchor(
    diff: Dict[str, DiffFile], path: str, line: int, side: Optional[str] = None
) -> Optional[Tuple[int, str]]:
    """Return a (line, side) pair that GitHub will accept, or None."""
    target = diff.get(path)
    if target is None:
        return None

    if side == "LEFT" and line in target.left:
        return line, "LEFT"
    if side == "RIGHT" and line in target.right:
        return line, "RIGHT"

    if line in target.right:
        return line, "RIGHT"
    if line in target.left:
        return line, "LEFT"

    # The finding points just outside the hunk. Anchor it to the nearest line
    # that is actually in the diff, preferring added lines.
    right = sorted(
        target.right,
        key=lambda value: (abs(value - line), 0 if value in target.added else 1),
    )
    if right:
        return right[0], "RIGHT"

    left = sorted(target.left, key=lambda value: abs(value - line))
    if left:
        return left[0], "LEFT"

    return None


# ---------------------------------------------------------------------------
# model output
# ---------------------------------------------------------------------------


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = ANSI_RE.sub("", text or "").strip()
    if not text:
        return None

    candidates: List[str] = [text]
    candidates.extend(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S))

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    return None


def _clip(value: Any, limit: int) -> str:
    return str(value if value is not None else "").strip()[:limit]


def normalize_output(raw: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    findings: List[Dict[str, Any]] = []
    general: List[Dict[str, Any]] = []

    for item in raw.get("findings") or []:
        if not isinstance(item, dict):
            continue
        path = _clip(item.get("path"), 500)
        try:
            line = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        severity = _clip(item.get("severity"), 20).lower()
        body = _clip(item.get("body"), 1500)
        standard = _clip(item.get("standard"), 1000)
        fix = _clip(item.get("fix"), 1000)
        if not path or line < 1 or severity not in SEVERITY_ORDER:
            continue
        if not body or not standard or not fix:
            continue
        finding = {
            "path": path,
            "line": line,
            "severity": severity,
            "body": body,
            "standard": standard,
            "fix": fix,
        }
        side = _clip(item.get("side"), 10).upper()
        if side in ("RIGHT", "LEFT"):
            finding["side"] = side
        title = _clip(item.get("title"), 120)
        if title:
            finding["title"] = title
        suggestion = item.get("suggestion")
        if isinstance(suggestion, str) and suggestion.strip():
            finding["suggestion"] = suggestion.strip()[:4000]
        findings.append(finding)

    for item in raw.get("general") or []:
        if not isinstance(item, dict):
            continue
        location = _clip(item.get("location"), 500)
        severity = _clip(item.get("severity"), 20).lower()
        body = _clip(item.get("body"), 1500)
        standard = _clip(item.get("standard"), 1000)
        fix = _clip(item.get("fix"), 1000)
        if not LOCATION_RE.match(location) or severity not in SEVERITY_ORDER:
            continue
        if not body or not standard or not fix:
            continue
        entry = {
            "location": location,
            "severity": severity,
            "body": body,
            "standard": standard,
            "fix": fix,
        }
        title = _clip(item.get("title"), 120)
        if title:
            entry["title"] = title
        general.append(entry)

    return {"findings": findings, "general": general}


def format_comment(finding: Dict[str, Any]) -> str:
    severity = SEVERITY_LABEL.get(finding["severity"], finding["severity"].title())
    title = finding.get("title")
    head = f"**{severity}**"
    if title:
        head += f" - {title.strip().rstrip('.')}"
    parts = [head, finding["body"].strip()]
    parts.append(f'**Standard:** "{finding["standard"].strip()}"')
    parts.append(f'**Fix:** {finding["fix"].strip()}')
    suggestion = finding.get("suggestion")
    if suggestion:
        parts.append(f"```suggestion\n{suggestion.strip()}\n```")
    parts.append(MARKER)
    return "\n\n".join(parts)


def format_general(entry: Dict[str, Any]) -> str:
    severity = SEVERITY_LABEL.get(entry["severity"], entry["severity"].title())
    title = f" - {entry['title'].strip().rstrip('.')}" if entry.get("title") else ""
    return (
        f"- `{entry['location']}` - **{severity}**{title}. {entry['body'].strip()} "
        f'(Standard: "{entry["standard"].strip()}"; Fix: {entry["fix"].strip()})'
    )


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def existing_keys(comments: Iterable[Dict[str, Any]]) -> set[Tuple[str, int, str]]:
    keys: set[Tuple[str, int, str]] = set()
    for comment in comments:
        body = comment.get("body") or ""
        if MARKER not in body:
            continue
        path = comment.get("path")
        line = comment.get("line") or comment.get("original_line")
        if not path or not isinstance(line, int):
            continue
        keys.add((path, line, _body_hash(body)))
    return keys


def build_payload(
    output: Dict[str, List[Dict[str, Any]]],
    diff: Dict[str, DiffFile],
    *,
    repo: str,
    pr: int,
    head_sha: str,
    base_sha: str,
    min_severity: str,
    max_findings: int,
    existing: Iterable[Dict[str, Any]] = (),
    post_clean: bool = False,
    standards_ref: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    threshold = SEVERITY_ORDER[min_severity]
    seen = existing_keys(existing)
    ranked = sorted(
        output.get("findings", []),
        key=lambda item: (SEVERITY_ORDER[item["severity"]], item["severity"]),
        reverse=True,
    )
    inline: List[Dict[str, Any]] = []
    general = list(output.get("general", []))

    for finding in ranked:
        if SEVERITY_ORDER[finding["severity"]] < threshold:
            continue
        anchored = anchor(
            diff, finding["path"], finding["line"], finding.get("side")
        )
        if anchored is None:
            general.append(
                {
                    "location": f"{finding['path']}:{finding['line']}",
                    "severity": finding["severity"],
                    "title": finding.get("title", ""),
                    "body": finding["body"],
                    "standard": finding["standard"],
                    "fix": finding["fix"],
                }
            )
            continue
        line, side = anchored
        if len(inline) >= max_findings:
            general.append(
                {
                    "location": f"{finding['path']}:{line}",
                    "severity": finding["severity"],
                    "title": finding.get("title", ""),
                    "body": finding["body"],
                    "standard": finding["standard"],
                    "fix": finding["fix"],
                }
            )
            continue
        body = format_comment(finding)
        key = (finding["path"], line, _body_hash(body))
        if key in seen:
            continue
        seen.add(key)
        inline.append(
            {
                "path": finding["path"],
                "line": line,
                "side": side,
                "body": body,
            }
        )

    general = [
        item
        for item in general
        if SEVERITY_ORDER.get(item["severity"], -1) >= threshold
    ]

    if not inline and not general and not post_clean:
        return None

    lines = [MARKER, "**Coding-standards review**"]
    ref_label = standards_ref or base_sha[:12]
    lines.append(
        f"Reviewed `{repo}#{pr}` against `.github/copilot-instructions.md` at "
        f"`{ref_label}`."
    )
    if inline:
        lines.append(f"{len(inline)} inline finding(s).")
    else:
        lines.append("No inline findings.")
    if general:
        lines.append("")
        lines.append("### Findings outside the diff")
        lines.extend(format_general(item) for item in general)
    if not inline and not general:
        lines.append("No coding-standards violations found.")

    return {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": "\n\n".join(lines),
        "comments": inline,
    }


# ---------------------------------------------------------------------------
# opencode
# ---------------------------------------------------------------------------


def opencode_env(out_dir: Path) -> Dict[str, str]:
    """Give opencode its own config/data home so runs cannot collide.

    The managed service keeps a port per HOME; a private home plus
    --standalone means a review cannot fight a developer's local server.
    """
    env = os.environ.copy()
    home = out_dir / "home"
    config_dir = home / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HERE / "opencode.review.json", config_dir / "opencode.json")
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_STATE_HOME"] = str(home / ".local" / "state")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    env.setdefault("OPENCODE_DISABLE_AUTOUPDATE", "1")
    env.setdefault("OPENCODE_AUTO_SHARE", "false")
    return env


def run_agent(
    *,
    workdir: Path,
    out_dir: Path,
    prompt: str,
    pr: int,
    timeout: int,
    opencode_bin: str,
) -> Tuple[int, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / "events.jsonl"
    log_path = out_dir / "opencode.log"
    cmd = [
        opencode_bin,
        "run",
        "--agent",
        "pr-reviewer",
        "--format",
        "json",
        "--title",
        f"pr-review-{pr}",
        "--auto",
        prompt,
    ]
    with open(events_path, "w", encoding="utf-8") as stdout, open(
        log_path, "w", encoding="utf-8"
    ) as stderr:
        proc = subprocess.Popen(
            cmd,
            cwd=str(workdir),
            env=opencode_env(out_dir),
            stdout=stdout,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout), str(events_path)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            return -1, str(events_path)


def final_text(events_path: Path) -> str:
    """Extract the final assistant message from opencode's JSONL stream."""
    parts: Dict[str, Dict[str, Any]] = {}
    all_text: List[str] = []
    try:
        handle = open(events_path, encoding="utf-8")
    except FileNotFoundError:
        return ""
    with handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "text":
                continue
            part = event.get("part") or {}
            text = part.get("text") or ""
            if not text:
                continue
            all_text.append(text)
            part_id = part.get("id")
            message_id = part.get("messageID")
            if not part_id or message_id is None:
                continue
            timestamp = event.get("timestamp") or 0
            existing = parts.get(part_id)
            if existing is None or timestamp >= existing["timestamp"]:
                parts[part_id] = {
                    "messageID": message_id,
                    "timestamp": timestamp,
                    "text": text,
                }

    if not parts:
        return "".join(all_text)

    messages: Dict[Any, List[Dict[str, Any]]] = {}
    for part in parts.values():
        messages.setdefault(part["messageID"], []).append(part)
    if not messages:
        return "".join(all_text)
    last = max(messages, key=lambda key: max(p["timestamp"] for p in messages[key]))
    return "".join(
        p["text"] for p in sorted(messages[last], key=lambda p: p["timestamp"])
    )


# ---------------------------------------------------------------------------
# review payload I/O and posting
# ---------------------------------------------------------------------------


def render_markdown(payload: Dict[str, Any], meta: Dict[str, Any]) -> str:
    lines = [
        f"# PR #{meta.get('pr')} - {meta.get('title', '')}",
        "",
        f"- URL: {meta.get('url', '')}",
        f"- Base: `{meta.get('base_sha', '')}`",
        f"- Head: `{meta.get('head_sha', '')}`",
        f"- Inline findings: {len(payload.get('comments', []))}",
        "",
        payload.get("body", ""),
        "",
        "## Inline comments",
        "",
    ]
    for comment in payload.get("comments", []):
        lines.append(f"### `{comment['path']}:{comment['line']}` ({comment['side']})")
        lines.append("")
        lines.append(comment["body"].replace(MARKER, "").strip())
        lines.append("")
    return "\n".join(lines)


def write_outputs(
    out_dir: Path,
    payload: Optional[Dict[str, Any]],
    meta: Dict[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if payload is None:
        (out_dir / "review.json").write_text("null\n")
        (out_dir / "review.md").write_text("No coding-standards findings.\n")
        summary = "No coding-standards findings."
    else:
        (out_dir / "review.json").write_text(json.dumps(payload, indent=2) + "\n")
        (out_dir / "review.md").write_text(render_markdown(payload, meta))
        summary = (
            f"{len(payload.get('comments', []))} inline comment(s); "
            f"event={payload.get('event')}"
        )
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as handle:
            handle.write(f"## Coding-standards review\n\n{summary}\n\n")
            if payload and payload.get("comments"):
                for comment in payload["comments"]:
                    handle.write(
                        f"- `{comment['path']}:{comment['line']}` "
                        f"({comment['side']})\n"
                    )
            handle.write("\n")


def post_review(repo: str, pr: int, payload: Dict[str, Any], out_dir: Path) -> None:
    payload_path = out_dir / "review.json"
    proc = run(
        [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repo}/pulls/{pr}/reviews",
            "--input",
            str(payload_path),
        ],
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "failed to post review:\n" + (proc.stderr or proc.stdout)[-4000:]
        )


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def build_prompt(
    *,
    repo: str,
    pr: int,
    title: str,
    base_sha: str,
    head_sha: str,
    standards_path: Path,
    diff_path: Path,
    meta_path: Path,
    max_findings: int,
    min_severity: str,
) -> str:
    template = (HERE / "prompt.md").read_text(encoding="utf-8")
    values = {
        "{{REPO}}": repo,
        "{{PR_NUMBER}}": str(pr),
        "{{PR_TITLE}}": title,
        "{{BASE_SHA}}": base_sha,
        "{{HEAD_SHA}}": head_sha,
        "{{STANDARDS_PATH}}": str(standards_path),
        "{{DIFF_PATH}}": str(diff_path),
        "{{META_PATH}}": str(meta_path),
        "{{MAX_FINDINGS}}": str(max_findings),
        "{{MIN_SEVERITY}}": min_severity,
    }
    for key, value in values.items():
        template = template.replace(key, value)
    return template


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    pr_data = gh_json("api", f"repos/{args.repo}/pulls/{args.pr}")
    base_sha = args.base_sha or pr_data["base"]["sha"]
    head_sha = args.head_sha or pr_data["head"]["sha"]
    title = pr_data.get("title") or ""
    url = pr_data.get("html_url") or ""

    if args.checkout:
        git("fetch", "origin", f"pull/{args.pr}/head:refs/remotes/pr/{args.pr}", cwd=workdir)
        git("checkout", "--detach", head_sha, cwd=workdir)

    actual = git("rev-parse", "HEAD", cwd=workdir).strip()
    if actual != head_sha:
        print(
            f"warning: worktree HEAD is {actual[:12]}, expected {head_sha[:12]}",
            file=sys.stderr,
        )

    standards_ref = None
    if args.standards:
        standards = Path(args.standards).read_text(encoding="utf-8")
    else:
        # Prefer the base commit. A PR whose base predates the rulebook (or a
        # demo PR based on an old commit) falls back to the default branch,
        # then the head commit, rather than failing the review.
        default_branch = None
        try:
            default_branch = gh_json("api", f"repos/{args.repo}").get("default_branch")
        except (RuntimeError, json.JSONDecodeError, AttributeError):
            pass
        standards = None
        standards_ref = None
        for ref in (base_sha, default_branch, head_sha):
            if not ref:
                continue
            try:
                standards = gh_raw(
                    f"repos/{args.repo}/contents/.github/copilot-instructions.md?ref={ref}"
                )
                standards_ref = ref
                print(f"standards from {ref[:12]}")
                break
            except RuntimeError:
                continue
        if standards is None:
            for ref in (base_sha, head_sha):
                try:
                    standards = git(
                        "show", f"{ref}:.github/copilot-instructions.md", cwd=workdir
                    )
                    standards_ref = ref
                    break
                except RuntimeError:
                    continue
        if standards is None:
            raise RuntimeError("could not read .github/copilot-instructions.md")

    standards_path = out_dir / "standards.md"
    standards_path.write_text(standards, encoding="utf-8")

    if args.diff:
        diff_text = Path(args.diff).read_text(encoding="utf-8")
    else:
        diff_text = gh("pr", "diff", str(args.pr), "-R", args.repo)
    diff_path = out_dir / "diff.patch"
    diff_path.write_text(diff_text, encoding="utf-8")

    meta = {
        "repo": args.repo,
        "pr": args.pr,
        "title": title,
        "url": url,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files": len(pr_data.get("files", [])) if isinstance(pr_data, dict) else None,
    }
    meta_path = out_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    prompt = build_prompt(
        repo=args.repo,
        pr=args.pr,
        title=title,
        base_sha=base_sha,
        head_sha=head_sha,
        standards_path=standards_path,
        diff_path=diff_path,
        meta_path=meta_path,
        max_findings=args.max_findings,
        min_severity=args.min_severity,
    )
    (out_dir / "task.md").write_text(prompt, encoding="utf-8")

    print(f"reviewing {args.repo}#{args.pr} at {head_sha[:12]}")
    rc, events_path = run_agent(
        workdir=workdir,
        out_dir=out_dir,
        prompt=prompt,
        pr=args.pr,
        timeout=args.timeout,
        opencode_bin=args.opencode_bin,
    )
    raw = final_text(Path(events_path))
    (out_dir / "agent-output.txt").write_text(raw, encoding="utf-8")

    if rc != 0 or not raw.strip():
        print(f"opencode failed (rc={rc}); see {out_dir / 'opencode.log'}", file=sys.stderr)
        return 1

    parsed = extract_json(raw)
    if parsed is None:
        print(f"agent output was not valid JSON; see {out_dir / 'agent-output.txt'}", file=sys.stderr)
        return 1

    normalized = normalize_output(parsed)
    (out_dir / "findings.json").write_text(
        json.dumps(normalized, indent=2) + "\n", encoding="utf-8"
    )

    existing: List[Dict[str, Any]] = []
    if not args.no_existing:
        try:
            existing = json.loads(
                gh("api", "--paginate", f"repos/{args.repo}/pulls/{args.pr}/comments")
                or "[]"
            )
        except (RuntimeError, json.JSONDecodeError):
            existing = []

    diff = parse_diff(diff_text)
    payload = build_payload(
        normalized,
        diff,
        repo=args.repo,
        pr=args.pr,
        head_sha=head_sha,
        base_sha=base_sha,
        min_severity=args.min_severity,
        max_findings=args.max_findings,
        existing=existing,
        standards_ref=standards_ref,
    )
    write_outputs(out_dir, payload, meta)

    if payload is None:
        print("no findings; nothing to post")
        return 0

    print(
        f"{len(payload['comments'])} inline comment(s), "
        f"review event {payload['event']}"
    )
    if args.dry_run:
        print("dry-run: not posting")
        return 0

    if pr_data.get("state") == "closed" and not args.allow_closed:
        print("PR is closed; refusing to post (use --allow-closed to override)")
        return 1

    post_review(args.repo, args.pr, payload, out_dir)
    print("posted review")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).resolve()
    findings = json.loads(Path(args.findings).read_text(encoding="utf-8"))
    normalized = normalize_output(findings)
    diff_text = Path(args.diff).read_text(encoding="utf-8")
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    payload = build_payload(
        normalized,
        parse_diff(diff_text),
        repo=meta.get("repo", ""),
        pr=int(meta.get("pr", 0)),
        head_sha=meta.get("head_sha", ""),
        base_sha=meta.get("base_sha", ""),
        min_severity=args.min_severity,
        max_findings=args.max_findings,
        existing=[],
        standards_ref=meta.get("standards_ref"),
    )
    write_outputs(out_dir, payload, meta)
    print("wrote review.json and review.md")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    if payload is None:
        print("payload is null (no findings)")
        return 0
    diff_text = Path(args.diff).read_text(encoding="utf-8")
    diff = parse_diff(diff_text)
    errors: List[str] = []
    for comment in payload.get("comments", []):
        path = comment.get("path")
        line = comment.get("line")
        side = comment.get("side", "RIGHT")
        if path not in diff:
            errors.append(f"{path}: file is not in the diff")
            continue
        if not isinstance(line, int):
            errors.append(f"{path}: line is not an integer")
            continue
        if anchor(diff, path, line, side) != (line, side):
            errors.append(f"{path}:{line} ({side}) is not a valid diff anchor")
    if errors:
        print("invalid review payload:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"ok: {len(payload.get('comments', []))} inline comment(s) anchor")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="review a PR")
    run_parser.add_argument("--repo", required=True)
    run_parser.add_argument("--pr", required=True, type=int)
    run_parser.add_argument("--workdir", default=".")
    run_parser.add_argument("--out-dir", default="/tmp/pr-review")
    run_parser.add_argument("--base-sha")
    run_parser.add_argument("--head-sha")
    run_parser.add_argument("--standards")
    run_parser.add_argument("--diff")
    run_parser.add_argument("--min-severity", choices=["low", "medium", "high"], default="medium")
    run_parser.add_argument("--max-findings", type=int, default=8)
    run_parser.add_argument("--timeout", type=int, default=1200)
    run_parser.add_argument("--opencode-bin", default="opencode")
    run_parser.add_argument("--checkout", action="store_true")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--allow-closed", action="store_true")
    run_parser.add_argument("--no-existing", action="store_true")
    run_parser.set_defaults(func=cmd_run)

    render_parser = sub.add_parser("render", help="render a findings JSON")
    render_parser.add_argument("--findings", required=True)
    render_parser.add_argument("--diff", required=True)
    render_parser.add_argument("--meta", required=True)
    render_parser.add_argument("--out-dir", default="/tmp/pr-review")
    render_parser.add_argument("--min-severity", choices=["low", "medium", "high"], default="medium")
    render_parser.add_argument("--max-findings", type=int, default=8)
    render_parser.set_defaults(func=cmd_render)

    validate_parser = sub.add_parser("validate", help="validate a review payload")
    validate_parser.add_argument("--payload", required=True)
    validate_parser.add_argument("--diff", required=True)
    validate_parser.set_defaults(func=cmd_validate)

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
