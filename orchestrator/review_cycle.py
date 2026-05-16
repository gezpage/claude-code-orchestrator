# Review cycle manager; runs reviewer feedback rounds up to the configured cycle limit (_MAX_CYCLES).
import re
import subprocess
import time
from pathlib import Path

import yaml

from orchestrator import plan as plan_mod
from orchestrator import state as state_mod
from orchestrator.agent_runner import AgentRunner
from orchestrator.logger import OrchestratorLogger
from orchestrator.run_stage import run_stage

_MAX_CYCLES = 2

# Tracks findings across all reviewers and cycles for plan.md summary.
# Structure: {reviewer: [(finding_text, resolved_in_cycle_or_None)]}
_FindingsMap = dict[str, list[tuple[str, int | None]]]


def _parse_frontmatter(content: str):
    if content.startswith("---\n"):
        end = content.find("\n---\n", 4)
        if end >= 0:
            return yaml.safe_load(content[4:end]) or {}, content[end + 5 :]
    return {}, content


def _write_frontmatter(meta: dict, body: str) -> str:
    return "---\n" + yaml.dump(meta, default_flow_style=False) + "---\n" + body


def _extract_changes_sections(content: str, reviewers: list) -> str:
    """Return the markdown heading blocks for the given reviewers."""
    _, body = _parse_frontmatter(content)
    sections = []
    lines = body.splitlines(keepends=True)
    capturing = False
    current: list[str] = []
    heading_re = re.compile(r"^#{1,3}\s+(.+)")

    for line in lines:
        m = heading_re.match(line)
        if m:
            if capturing and current:
                sections.append("".join(current))
            heading_text = m.group(1).strip()
            capturing = any(r.lower() in heading_text.lower() for r in reviewers)
            current = [line] if capturing else []
        elif capturing:
            current.append(line)

    if capturing and current:
        sections.append("".join(current))

    return "\n".join(sections)


def _update_review_md(review_md_path: Path, reviewer: str, verdict: str, round_num: int, content: str) -> None:
    if review_md_path.exists():
        existing = review_md_path.read_text()
    else:
        existing = "---\nreviewer_statuses: {}\n---\n"

    meta, body = _parse_frontmatter(existing)
    meta.setdefault("reviewer_statuses", {})[reviewer] = verdict
    section = f"\n## {reviewer.title()} Review — Round {round_num}\n\n{content}\n"
    review_md_path.write_text(_write_frontmatter(meta, body + section))


def _chronological_hashes(repo_root: str, hashes: list[str]) -> list[str]:
    """Sort commit hashes oldest-first using git's topological order.

    Agents may emit `commit_hashes` in any order (notably `git log` default is newest-first).
    `first^..last` only produces the intended forward diff when `first` is the topological
    ancestor, so callers must not rely on the agent's ordering. Falls back to the input
    order if git fails (so an invalid hash is surfaced by the downstream diff command
    rather than swallowed here)."""
    if len(hashes) <= 1 or not repo_root:
        return list(hashes)
    result = subprocess.run(
        ["git", "-C", repo_root, "rev-list", "--no-walk", "--topo-order", "--reverse", *hashes],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return list(hashes)
    return result.stdout.strip().splitlines()


def compute_stage_diff(repo_root: str, commit_hashes: list[str]) -> str:
    """Return the unified diff text covering all commits in `commit_hashes`.

    Sorts hashes topologically before computing `first^..last`, so agents that report
    newest-first (or any other order) still produce a forward diff. Returns '' when
    there are no commits or repo_root is missing."""
    if not commit_hashes or not repo_root:
        return ""
    ordered = _chronological_hashes(repo_root, commit_hashes)
    first, last = ordered[0], ordered[-1]
    diff_result = subprocess.run(
        ["git", "-C", repo_root, "diff", f"{first}^..{last}"],
        capture_output=True,
        text=True,
    )
    return diff_result.stdout


def _write_round_diff(run_folder: Path, repo_root: str, commit_hashes: list[str], round_num: int) -> str:
    """Compute the real git diff for this fix cycle and persist it to review/diff-round-N.patch.

    Reviewers must operate on the actual patch, not on a prose summary returned by the fix
    agent. Returns the absolute path string (empty if no commits or repo_root)."""
    if not commit_hashes or not repo_root:
        return ""
    diff_text = compute_stage_diff(repo_root, commit_hashes)
    diff_path = run_folder / "review" / f"diff-round-{round_num}.patch"
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_text)
    return str(diff_path)


def _head_sha(repo_root: str) -> str:
    """Return the current HEAD commit SHA, or '' if the call fails."""
    if not repo_root:
        return ""
    result = subprocess.run(
        ["git", "-C", repo_root, "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _commits_since(repo_root: str, before_sha: str) -> list[str]:
    """Return commit SHAs reachable from HEAD but not from before_sha, in chronological order."""
    if not repo_root or not before_sha:
        return []
    result = subprocess.run(
        ["git", "-C", repo_root, "rev-list", "--reverse", f"{before_sha}..HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _render_findings_brief(findings_map: _FindingsMap) -> str:
    """Render unresolved findings as a markdown brief for the fix-implementation prompt.

    Fallback for when review-log.md is missing the expected `## <Reviewer> Review — Round N`
    headings (a fragile contract — reviewer agents sometimes write nothing or use a different
    heading format). The in-memory findings_map is authoritative for round-1 findings, so
    rendering from it guarantees the fix agent has something concrete to act on."""
    sections: list[str] = []
    for reviewer, findings in findings_map.items():
        unresolved = [text for text, resolved in findings if resolved is None]
        if not unresolved:
            continue
        sections.append(f"## {reviewer.title()} Review\n")
        for text in unresolved:
            sections.append(f"- {text}")
        sections.append("")
    return "\n".join(sections)


def is_valid_diff_file(path: str) -> bool:
    """Return True if `path` is a usable git-diff file for a reviewer.

    Used at the orchestrator level to fail review stages deterministically when the
    diff input is missing, unreadable, empty, or a prose summary rather than a real
    git diff. Prompt-level rejection alone is unreliable: an LLM reviewer may still
    attempt a speculative review on an invalid input. This check runs before the
    reviewer is dispatched so it cannot."""
    if not path:
        return False
    p = Path(path)
    if not p.is_file():
        return False
    try:
        head = p.read_text(errors="replace")[:4096]
    except OSError:
        return False
    if not head.strip():
        return False
    return "diff --git" in head


def _inject_fix_divider(review_md_path: Path, cycle_num: int, commit_messages: list[str]) -> None:
    """Insert a fix-cycle commit marker into review-log.md before the next review round.

    ``cycle_num`` is the 1-indexed fix cycle that produced these commits (1, 2). The
    label here intentionally tracks the fix run (Fix Cycle N), not the next review round
    (Review Round N+1) — conflating the two has caused user-facing labels like
    ``Fix Cycle 3`` to appear after only two fix runs."""
    if not review_md_path.exists():
        return
    commits_str = ", ".join(f"`{c}`" for c in commit_messages) if commit_messages else "no commits"
    divider = f"\n---\n**Fix Cycle {cycle_num}:** {commits_str}\n\n---\n"
    review_md_path.write_text(review_md_path.read_text() + divider)


def append_findings_summary(
    plan_path: Path,
    findings_map: _FindingsMap,
    reviewer_statuses: dict,
    accepted_risks: dict[str, list[str]] | None = None,
) -> None:
    """Append a Review Findings section to plan.md after all cycles complete.

    Blocking findings come from ``findings_map`` (with per-cycle resolution status).
    Non-blocking findings come from ``accepted_risks`` and are persisted as accepted risks
    so the final summary makes them visible even when no fix cycle ran."""
    if not plan_path.exists():
        return
    if not findings_map and not accepted_risks:
        return

    lines = ["\n## Review Findings\n"]
    for reviewer, findings in findings_map.items():
        if not findings:
            final_verdict = reviewer_statuses.get(reviewer, "approved")
            lines.append(f"**{reviewer}** — {final_verdict}, no blocking issues\n")
            continue
        lines.append(f"**{reviewer}:**\n")
        for text, resolved_cycle in findings:
            if resolved_cycle is not None:
                # resolved_cycle is the 1-indexed fix cycle that produced the commits the
                # reviewer then approved; the approving review ran in Review Round N+1.
                lines.append(
                    f"- {text} → Fixed by Fix Cycle {resolved_cycle} (approved in Review Round {resolved_cycle + 1})"
                )
            else:
                lines.append(f"- {text} → Unresolved")
        lines.append("")

    if accepted_risks and any(accepted_risks.values()):
        lines.append("### Accepted Risks (non-blocking)\n")
        for reviewer, risks in accepted_risks.items():
            if not risks:
                continue
            lines.append(f"**{reviewer}:**\n")
            for text in risks:
                lines.append(f"- {text}")
            lines.append("")

    section = "\n".join(lines)
    content = plan_path.read_text()
    markers = ["\n## File Manifest", "\n## Run Summary"]
    insert_at = len(content)
    for marker in markers:
        idx = content.find(marker)
        if 0 <= idx < insert_at:
            insert_at = idx

    if insert_at < len(content):
        plan_path.write_text(content[:insert_at] + section + content[insert_at:])
    else:
        plan_path.write_text(content + section)


# Back-compat alias for callers/tests that referenced the private name.
_append_findings_summary = append_findings_summary


def run(
    run_folder,
    docs_root,
    project,
    branch,
    review_signal,
    project_log_path,
    repo_root: str = "",
    *,
    implementation_runner: AgentRunner | None = None,
    review_runner: AgentRunner | None = None,
) -> dict:
    run_folder = Path(run_folder)
    logger = OrchestratorLogger(run_folder, str(project_log_path))

    signals = state_mod.load_signals(run_folder)
    context_path = signals.get("specification", {}).get("context_path", "")
    verification_signal = signals.get("verification", {})
    if not isinstance(verification_signal, dict):
        verification_signal = {}
    verify_md_path = verification_signal.get("verify_md_path", "")
    verification_status = verification_signal.get("verification_status", "")

    reviewer_statuses = dict(review_signal.get("reviewer_statuses", {}))
    changes_requested = [r for r, s in reviewer_statuses.items() if s == "changes-requested"]
    accepted_risks: dict[str, list[str]] = dict(review_signal.get("reviewer_non_blocking_findings", {}) or {})

    if not changes_requested:
        return {"all_passed": True, "reviewer_statuses": reviewer_statuses}

    review_md_path = run_folder / "review" / "review-log.md"
    review_md_path.parent.mkdir(parents=True, exist_ok=True)

    # Seed findings from round-1 signals; resolved_cycle filled in when a later review approves.
    findings_map: _FindingsMap = {
        r: [(f, None) for f in review_signal.get("reviewer_findings", {}).get(r, [])] for r in changes_requested
    }

    for cycle in range(1, _MAX_CYCLES + 1):
        round_num = cycle + 1  # Round 2 for first cycle, Round 3 for second

        changes_brief = ""
        if review_md_path.exists():
            changes_brief = _extract_changes_sections(review_md_path.read_text(), changes_requested)
        if not changes_brief.strip():
            # review-log.md was missing or had no matching reviewer sections — fall back to
            # the in-memory findings so the fix agent still receives an actionable brief.
            changes_brief = _render_findings_brief(findings_map)

        plan_mod.add_fix_cycle_node(run_folder, cycle, changes_requested)

        fix_vars = {
            "run_folder": str(run_folder),
            "docs_root": docs_root,
            "branch": branch,
            "changes_brief": changes_brief,
            "repo_root": repo_root,
        }
        # Capture HEAD before the fix run so we can detect commits via git rather than trust
        # the agent's commit_hashes self-report (which has been observed to be empty even
        # when the agent should have produced commits — and could be wrong in the other
        # direction too).
        before_head = _head_sha(repo_root)
        fix_t0 = time.monotonic()
        fix_sig = run_stage(
            "fix-implementation",
            "default",
            fix_vars,
            run_folder,
            docs_root,
            project,
            str(project_log_path),
            output_suffix=str(cycle),
            cwd=repo_root or None,
            runner=implementation_runner,
        )
        fix_elapsed = time.monotonic() - fix_t0
        fix_status = fix_sig.get("status", "unknown")
        reported_hashes = fix_sig.get("commit_hashes", []) or []
        actual_hashes = _commits_since(repo_root, before_head)
        # Git is the source of truth. Prefer the actual list; only fall back to the agent's
        # self-report if we couldn't query git (e.g. repo_root unavailable in unit tests).
        commit_hashes = actual_hashes if before_head else reported_hashes
        fix_summary = f"{len(commit_hashes)} commit{'s' if len(commit_hashes) != 1 else ''}" if commit_hashes else None
        plan_mod.update_plan_md(
            run_folder,
            f"fix_impl_{cycle}",
            "passed" if fix_status == "passed" and commit_hashes else "blocked",
            elapsed_secs=fix_elapsed,
            output_summary=fix_summary,
        )
        logger.log("review-cycle", "INFO", f"fix-implementation round {round_num}: {fix_status}")

        commit_messages = fix_sig.get("commit_messages", commit_hashes)
        _inject_fix_divider(review_md_path, cycle, commit_messages)

        diff_path = _write_round_diff(run_folder, repo_root, commit_hashes, round_num)

        # Deterministic gate: if the fix cycle produced no usable diff, fail the cycle here
        # rather than dispatch reviewers against an empty or non-diff input.
        if not is_valid_diff_file(diff_path):
            if not commit_hashes:
                msg = (
                    f"review-cycle round {round_num} aborted: fix-implementation made no commits "
                    f"(agent status={fix_status!r}, reported_hashes={reported_hashes!r}). "
                    f"See {run_folder / 'fix-implementation' / f'fix-implementation-{cycle}-output.md'} "
                    f"for the agent's reasoning."
                )
            else:
                msg = (
                    f"review-cycle round {round_num} aborted: no valid git diff at {diff_path!r} "
                    f"for commits {commit_hashes!r}"
                )
            logger.log("review-cycle", "ERROR", msg)
            append_findings_summary(
                run_folder / "plan.md", findings_map, reviewer_statuses, accepted_risks=accepted_risks
            )
            return {
                "all_passed": False,
                "blocked": True,
                "reviewers": changes_requested,
                "reviewer_statuses": reviewer_statuses,
                "message": msg,
            }

        for reviewer in list(changes_requested):
            review_vars = {
                "run_folder": str(run_folder),
                "docs_root": docs_root,
                "review_md": str(review_md_path),
                "diff": diff_path,
                "round": str(round_num),
                "context_path": context_path,
                "repo_root": repo_root,
                "verify_md_path": verify_md_path,
                "verification_status": verification_status,
            }
            review_t0 = time.monotonic()
            sig = run_stage(
                "review",
                reviewer,
                review_vars,
                run_folder,
                docs_root,
                project,
                str(project_log_path),
                output_suffix=f"{reviewer}-round{round_num}",
                cwd=repo_root or None,
                runner=review_runner,
            )
            review_elapsed = time.monotonic() - review_t0
            verdict = sig.get("reviewer_statuses", {}).get(reviewer, sig.get("status", "unknown"))
            reviewer_statuses[reviewer] = verdict
            round_non_blocking = sig.get("non_blocking_findings", [])
            if isinstance(round_non_blocking, list) and round_non_blocking:
                # Merge later-round non-blocking findings into accepted risks, de-duplicating.
                existing = accepted_risks.setdefault(reviewer, [])
                for text in round_non_blocking:
                    if text not in existing:
                        existing.append(text)
            _update_review_md(review_md_path, reviewer, verdict, round_num, sig.get("message", ""))
            plan_mod.update_plan_md(
                run_folder,
                f"review_{reviewer}_{round_num}",
                "blocked" if verdict == "changes-requested" else "passed",
                elapsed_secs=review_elapsed,
                output_summary=verdict,
            )
            logger.log("review-cycle", "INFO", f"reviewer {reviewer} round {round_num}: {verdict}")

            if verdict == "approved":
                findings_map[reviewer] = [
                    (text, cycle if resolved is None else resolved) for text, resolved in findings_map.get(reviewer, [])
                ]

        state_mod.update_stage_status(run_folder, f"review-cycle-{cycle}", "passed")

        changes_requested = [r for r, s in reviewer_statuses.items() if s == "changes-requested"]
        if not changes_requested:
            append_findings_summary(
                run_folder / "plan.md", findings_map, reviewer_statuses, accepted_risks=accepted_risks
            )
            return {"all_passed": True, "reviewer_statuses": reviewer_statuses}

    append_findings_summary(run_folder / "plan.md", findings_map, reviewer_statuses, accepted_risks=accepted_risks)
    return {
        "all_passed": False,
        "blocked": True,
        "reviewers": changes_requested,
        "reviewer_statuses": reviewer_statuses,
    }
