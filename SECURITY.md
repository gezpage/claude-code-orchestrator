# Security Policy

Orchestrator is a pipeline runner that dispatches Claude Code agents with broad filesystem and shell access. This document explains the security model, how to report vulnerabilities, and how to run the tool safely.

For deeper analysis of trust boundaries, attack surface, and known unsafe modes, see [`docs/threat-model.md`](docs/threat-model.md).

---

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security problems.

- Email the maintainer at **gezpage@gmail.com** with the subject `orchestrator security:` and a short description.
- Alternatively, open a private security advisory via GitHub: *Security → Report a vulnerability* on the [repository page](https://github.com/gezpage/claude-code-orchestrator/security/advisories/new).
- Expect an acknowledgement within 7 days. Coordinated disclosure preferred; we will agree a disclosure window together.

Please include:

- Affected version or commit SHA
- Reproduction steps or a proof-of-concept
- The impact you observed (data exfiltration, code execution outside expected scope, credential exposure, etc.)
- Any suggested mitigation

If the issue is in **Claude Code itself** (the upstream CLI this project orchestrates) rather than in this repository, report it directly to Anthropic instead.

---

## Threat model summary

Orchestrator is a **single-tenant, trusted-operator** tool. It is intentionally not hardened against:

- A malicious operator running it on their own machine.
- A malicious or untrusted feature spec authored to steer agents into harmful actions (prompt injection from `overview.md`, alignment logs, or any other input file).
- A compromised upstream Claude Code binary, or a compromised Anthropic API endpoint.
- Multi-tenant or shared-host execution where multiple users share a `docs-root` or `repo-root`.

It **is** designed to be safe when:

- A single trusted developer (or a trusted CI agent) invokes it on their own workstation or a dedicated CI runner.
- The `repo-root` and `docs-root` paths point at repositories under the operator's control.
- Inputs (feature specs, alignment logs) are authored by trusted humans.
- The host is isolated enough that an agent going off-script cannot harm anything outside the project scope.

The full trust model, including every assumption Orchestrator makes about its host, is documented in [`docs/threat-model.md`](docs/threat-model.md).

---

## Safe execution guidance

### Run inside isolation

Strongly recommended for any non-trivial run:

- **Container**: run Orchestrator inside a dedicated container image (Docker, Podman) with only the `repo-root` and `docs-root` mounted in.
- **devcontainer / Codespaces**: convenient for IDE-driven runs. Mount only what the pipeline needs.
- **Dedicated VM**: for the strongest isolation, run on a VM that does not hold credentials for unrelated services.

A built-in sandboxing layer is on the roadmap — see [issue #14](https://github.com/gezpage/claude-code-orchestrator/issues/14). Until that ships, isolation is the operator's responsibility.

### Limit blast radius

- Use a fresh git worktree or branch per run; never run against a dirty working tree on a long-lived branch.
- Ensure `repo-root` is a real repository (Orchestrator pre-validates this) — never point it at a directory containing unrelated work.
- Avoid running multiple feature pipelines that target the same repository concurrently from the same checkout.

### Review before merging

- Stage agents create commits, push branches, and open PRs on their own.
- The merge step is the human gate. Read the diff before merging.
- Do **not** wire Orchestrator output to an auto-merge bot. The fix-cycle limit (`_MAX_CYCLES = 2`, see [ADR-011](docs/adrs/ADR-011-fix-cycle-iteration-limit.md)) caps machine retries, but only a human should decide that code is fit to land.

### Watch the run

For long pipelines, periodically scan `run.log` and the per-stage `*-output.md` files for unexpected commands, unexpected file changes, or signs an agent has drifted from the spec.

---

## Credential handling

Orchestrator itself does **not** manage credentials. It inherits whatever credentials are present in the environment of the operator running it. That includes:

- The `claude` CLI's authenticated session (under `~/.claude/`).
- `git` credentials (SSH keys, `~/.gitconfig`, `~/.netrc`, OS keychain entries used by HTTPS remotes).
- `gh` CLI tokens and any `GITHUB_TOKEN` exported into the shell.
- Anything the Claude Code agent can Read on the host filesystem.

Guidance:

- **Do not commit secrets to `repo-root` or `docs-root`.** Stage agents read freely under these roots; anything there is visible to any stage.
- **Do not place secrets in `overview.md`, alignment logs, or any other input file.** Inputs flow into prompts; prompts flow into model context.
- **Prefer short-lived, narrowly-scoped tokens.** A run that takes hours has hours to misuse a long-lived token.
- **Rotate tokens after a run with unexpected behaviour.** Treat any odd agent action as a potential credential leak signal.
- **Use `.gitignore` aggressively.** Local `.env` files, `secrets/`, lockboxes, and similar should be ignored — and ideally not even live inside `repo-root`.

See [secret scanning guidance](#secret-scanning) below for tools that catch accidental commits.

---

## Sandbox and container recommendations

Minimum-viable container posture:

- Mount only `repo-root` and `docs-root`. Do not mount `$HOME`, `/`, or other unrelated directories.
- Run as a non-root user inside the container.
- Provide credentials via short-lived env vars or mounted secret files — not via a mounted SSH agent or keychain.
- Disable outbound network access to anything except `api.anthropic.com`, your git host, and any package registries the implementation stage truly needs.
- Snapshot the container image after each release so a bad run can be reproduced or rolled back.

Network egress is the hardest dimension to lock down because the implementation stage may legitimately need to fetch packages. Operators should decide on a per-project basis whether to allow general egress or to vendor dependencies first.

---

## Limitations of the current isolation model

Honest statement of what Orchestrator **does not** currently do:

- **No process sandboxing.** Stages are plain subprocesses inheriting the operator's environment, network, and filesystem access. The only enforced boundary is the working directory passed via `cwd`.
- **Permission gating, but no per-tool allow-list.** The Claude runner dispatches under `--permission-mode auto` (see [ADR-025](docs/adrs/ADR-025-remove-dangerously-skip-permissions.md)), the next-most-permissive mode short of `bypassPermissions`. Most tool uses are approved without prompting; there is no operator-maintained allow-list, and the threat model continues to treat stages as fully trusted subprocesses.
- **No content filtering on stage outputs.** A stage that writes a malicious file leaves it on disk; only human review catches this.
- **No prompt-injection defence.** Trust in input files is total. A feature spec that contains hostile instructions will be acted on.
- **No audit log signing.** `run.log` and `_state.yaml` are plain files; an agent with filesystem access can rewrite its own audit trail in principle.

These gaps are deliberate trade-offs for the unattended-pipeline use case, not oversights. Operator-level isolation (container, VM) is the intended mitigation. The hardening roadmap lives in [`docs/threat-model.md`](docs/threat-model.md).

---

## Unrestricted execution modes — warning

Stages dispatch under `--permission-mode auto` (Claude runner) or the configured Codex sandbox mode. `auto` approves most tool uses without prompting; the operator retains the audit trail of what was allowed but does not gate individual approvals. The threat model treats stage subprocesses as fully trusted — they can execute arbitrary shell commands, write files under `repo-root` / `docs-root`, and make git commits. The Codex backend with `--sandbox danger-full-access` (or the `full-auto` alias, which maps to `--dangerously-bypass-approvals-and-sandbox`) is the escape hatch for environments that need a fully permissive dispatch.

If that posture is unacceptable in your environment, do not run Orchestrator there. Run it in a container or VM that is itself unrestricted but quarantined.

---

## Secret scanning

Run a secret scanner before publishing this repository or any docs repository you point Orchestrator at:

- [`gitleaks`](https://github.com/gitleaks/gitleaks) — quick, broad ruleset, ideal for pre-commit and CI.
- [`trufflehog`](https://github.com/trufflesecurity/trufflehog) — entropy-based, catches some patterns gitleaks misses.

Suggested local one-liners:

```bash
# Gitleaks against the working tree
gitleaks detect --source . --no-banner

# TruffleHog against the full history
trufflehog git file://. --since-commit HEAD~100
```

CI-side scanning will be added once the threat model has been reviewed against actual usage patterns.

---

## Supported versions

This project is in early public release. Only the latest tagged release on `main` receives security fixes. If you are running an older tag, upgrade first.

---

## Acknowledgements

Security reports that lead to a fix will be credited (with the reporter's permission) in `CHANGELOG.md` and in the release notes of the fix.
