# Threat Model

This document describes the security model of Orchestrator: what it trusts, what it does not, where the boundaries are, and what it deliberately does not defend against. It complements [`SECURITY.md`](../SECURITY.md), which is operator-facing; this document is for security reviewers, contributors, and anyone embedding Orchestrator into a larger system.

For the load-bearing architectural decisions referenced here, see the relevant ADRs in [`docs/adrs/`](adrs/) — particularly [ADR-025](adrs/ADR-025-remove-dangerously-skip-permissions.md) (permission-mode auto; supersedes the earlier `--dangerously-skip-permissions` invariant from ADR-003), [ADR-004](adrs/ADR-004-oblivious-orchestrator.md) (token-minimisation invariant), and [ADR-022](adrs/ADR-022-claude-runners-oauth-only.md) (OAuth-only auth path; supersedes the earlier `--bare` invariant from ADR-012).

---

## Trust boundaries

Orchestrator's runtime has four distinct trust zones:

| Zone | Components | Trusted by | Trusts |
|------|------------|------------|--------|
| **Operator** | the human running `orchestrator run` | — (root of trust) | everything below |
| **Orchestrator process** | `orchestrate.py`, `run_stage.py`, CLI | Operator | stage subprocesses *to read/write files*, **not** to be honest about signal content (it is schema-validated) |
| **Stage subprocess** | a `claude <prompt> --permission-mode auto` invocation (Claude runner; see ADR-025) | Orchestrator process | the model, the host filesystem, and the operator's credentials |
| **Inputs** | `overview.md`, alignment logs, `findings.md`, prior signals, `project.yaml` | Stage subprocess | — |

The single hard trust boundary the orchestrator enforces is the **signal interface**: a stage subprocess communicates with the orchestrator only via one `SIGNAL_JSON:` sentinel line and the schema-validated dict it carries (see [ADR-002](adrs/ADR-002-signal-json-sentinel-line.md)). The orchestrator does not read stage output files — that is the token-minimisation invariant in [ADR-004](adrs/ADR-004-oblivious-orchestrator.md).

Everything else — filesystem, network, credentials — is in the same trust zone as the operator. There is no in-process privilege separation.

---

## Filesystem assumptions

Stage agents have **full Read/Write/Edit/Bash access** under:

- `repo-root` — the code repository.
- `docs-root` — the docs repository (run folders, project configs, profiles, prompts).
- The orchestrator's installed package directory (read-only in practice, but not enforced).
- The operator's `$HOME` directory and broader filesystem (Bash/Read/Write are not chrooted; only stages dispatched with `cwd=repo_root` are nudged toward the repo).

Assumptions Orchestrator relies on:

- Paths passed via `--docs-root` and `--repo-root` exist and are real directories.
- `repo-root` is a git working tree (`git rev-parse --git-dir` succeeds — see the pre-flight validator).
- Run folders under `{docs-root}/projects/{project}/workflow/runs/...` are not shared with other concurrent runs of the same feature.
- Files under run folders are append-only or single-writer per stage. Concurrent writes to `plan.md` from parallel slice agents are serialised via a threading lock, but no inter-process lock exists; running two `orchestrator run` invocations against the same run folder is undefined behaviour.

Not enforced:

- Stages can `cd` or write outside `repo-root` despite a `cwd=repo_root` setting. Operating-system-level isolation is the only barrier.
- Stages can delete or rewrite `_state.yaml`, `run.log`, and prior stage outputs. The audit trail is not tamper-evident.

---

## Subprocess execution model

Every stage is dispatched via `subprocess.Popen` (see `orchestrator/run_stage.py`) with:

- For the Claude runner: `claude <rendered prompt> --permission-mode auto` (`ClaudeCodeRunner`). Subprocess-piped stdout puts Claude Code into non-interactive mode automatically.
- `stdout`, `stderr` merged and captured.
- `cwd` set to either the run folder (default) or `repo-root` (when the profile sets `cwd_from_repo_root: true` for implementation/QA/review/fix-implementation stages).
- The orchestrator process's environment is inherited, **except** that the Claude runner strips `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` so a stale external key cannot override the operator's keychain/OAuth auth (see [ADR-022](adrs/ADR-022-claude-runners-oauth-only.md)).

`--permission-mode auto` keeps Claude's permission system engaged at its most permissive setting short of `bypassPermissions` (load-bearing — see [ADR-025](adrs/ADR-025-remove-dangerously-skip-permissions.md), which supersedes ADR-003's mandatory `--dangerously-skip-permissions`). `--bare` is **not** used by the Claude runner (see ADR-022): hooks, MCP servers, LSP, plugin sync, keychain reads, and `CLAUDE.md` auto-discovery are all active at stage time. The sterile-context default (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` plus an empty MCP config — see [ADR-023](adrs/ADR-023-sterile-context-suppresses-mcps.md)) suppresses ambient auto-memory injection and globally configured MCP servers.

Interactive stages (`mode: interactive`, see [ADR-007](adrs/ADR-007-alignment-pipeline-pause.md)) bypass `run_stage()` entirely; the orchestrator launches `claude` attached to the terminal, with no `--permission-mode` flag, and waits for the human to exit.

Implications:

- Any vulnerability in Claude Code itself, in the Anthropic model layer, or in upstream tools (Bash, git) is also a vulnerability in Orchestrator. There is no second line of defence.
- A misbehaving stage can fork long-running background processes; Orchestrator does not track or kill them after the stage signal is captured.
- A stage that exits with a non-zero status is treated as `blocked` only if no valid SIGNAL_JSON was emitted; a stage that emits `passed` and then crashes is treated as passed. The signal is the authoritative outcome, not the exit code (see [issue #23](https://github.com/gezpage/claude-code-orchestrator/issues/23) for tracked work to tighten this).

---

## Network assumptions

Orchestrator itself makes no network calls. Network exposure comes from:

- Claude Code's connection to `api.anthropic.com` for the model session.
- Whatever URLs the stage agent decides to fetch (the implementation stage commonly runs `git fetch`, package installs, etc.).
- Hooks and MCP servers — **active** at stage time for the Claude runners (see [ADR-022](adrs/ADR-022-claude-runners-oauth-only.md)), and active in interactive stages such as alignment. Operators who need them suppressed must disable them in Claude Code's own settings or switch to `codex_cli`.

Assumptions:

- Anthropic's API endpoint is trusted to deliver model output faithfully.
- The operator's network path to that endpoint is not adversarial.
- DNS, TLS, and certificate validation are handled correctly by upstream tools.

Not defended against:

- DNS rebinding or MITM of `api.anthropic.com`.
- A malicious package being installed by the implementation stage during `pip install`, `npm install`, etc.
- Data exfiltration by an agent that uploads run artefacts to an attacker-controlled host. Egress is the operator's perimeter to control.

---

## Credential handling

Orchestrator stores **no credentials**. It inherits:

- Claude Code's session token (`~/.claude/`).
- Git credentials (SSH agent, `~/.gitconfig`, `~/.netrc`, OS keychain).
- `gh` CLI tokens and any `GITHUB_TOKEN` in the environment.
- API keys, cloud credentials, and anything else the operator's shell exports.

A stage agent can read any of these if they are reachable from the filesystem or the environment. Operators must therefore:

- Treat the host environment as a single trust boundary. Anything the operator could do, an agent could do.
- Use short-lived tokens with the narrowest possible scope.
- Avoid running Orchestrator on hosts that hold credentials for unrelated, higher-value systems.
- Rotate credentials after any run that produced unexpected commits, file changes, or network activity.

See [`SECURITY.md` § Credential handling](../SECURITY.md#credential-handling) for operator-facing guidance.

---

## Sandbox expectations

Orchestrator assumes the **operator** provides isolation; the tool itself does not. Recommended deployment postures, in order of strength:

1. **Dedicated VM** with only the two repos and short-lived credentials. Cleanest blast-radius story.
2. **Container or devcontainer** with `repo-root` and `docs-root` mounted, non-root user, restricted egress.
3. **Host with a separate OS user** dedicated to Orchestrator, no SSH agent forwarding, no shared keychain.
4. **Direct host execution** — only acceptable on a single-user developer workstation where the operator already trusts every process they run.

Sandbox / isolation tooling integrated into Orchestrator itself is tracked in [issue #14](https://github.com/gezpage/claude-code-orchestrator/issues/14). Until that lands, isolation is operator-supplied.

---

## Known unsafe modes

Modes that are documented as unsafe and have no in-tree mitigation:

- **Untrusted feature specs.** Anything Orchestrator reads — `overview.md`, alignment logs, `findings.md`, prior signals — flows into a prompt. Treat any input file as code that will execute.
- **Concurrent runs against the same run folder.** State files are not locked across processes.
- **Auto-merging output PRs.** The pipeline ends with a PR open and a human gate. Wiring auto-merge defeats the design.
- **Long-lived credentials in scope.** A run that goes wrong on hour 4 has had hours to do harm with whatever tokens were reachable.
- **Running on a host with secrets in `$HOME` or the environment.** The stage agent can read both.
- **Interactive alignment with hostile content** in the rendered prompt or in MCP-tool output. All stage runs (autonomous as well as interactive — see [ADR-022](adrs/ADR-022-claude-runners-oauth-only.md)) fire any locally-configured hooks.
- **Trusting `_state.yaml`, `run.log`, or stage output files as a tamper-evident audit trail.** They are plain files.

---

## Hardening roadmap

These are tracked or planned items, not commitments:

- **Process-level sandbox for stage subprocesses** ([issue #14](https://github.com/gezpage/claude-code-orchestrator/issues/14)) — a container or `bwrap`-style wrapper invoked by `run_stage()` so each stage runs with bounded filesystem and network access.
- **Operational trust boundaries and human-ownership model docs** ([issue #32](https://github.com/gezpage/claude-code-orchestrator/issues/32)) — formalising who is responsible for which decisions at which stage.
- **Subprocess exit-code handling and transcript integrity** ([issue #23](https://github.com/gezpage/claude-code-orchestrator/issues/23)) — making non-zero exits and missing/corrupt signal lines harder to ignore.
- **Transactional integration branch strategy for parallel slices** ([issue #25](https://github.com/gezpage/claude-code-orchestrator/issues/25)) — reducing the window where partially-applied parallel slices can leave the integration branch in a half-merged state.
- **Per-stage model routing policy** ([issue #34](https://github.com/gezpage/claude-code-orchestrator/issues/34)) — letting safety-critical stages opt into stronger models or stricter prompts independently of cost-sensitive stages.
- **Secret scanning in CI** — gitleaks/trufflehog wired into the project's own CI, and a recommended pattern for downstream projects.
- **Audit log signing or append-only storage** — making `run.log` and `_state.yaml` harder for a misbehaving stage to rewrite silently.

This list will evolve as real usage surfaces issues. Contributions and reports welcome — see [`SECURITY.md` § Reporting a vulnerability](../SECURITY.md#reporting-a-vulnerability).
