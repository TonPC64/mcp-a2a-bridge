---
name: contribute
description: Test, secure, and submit project changes.
version: 0.1.0
author: TonPC64, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Contributing, Testing, Security]
    related_skills: [setup]
---

# Contribute Skill

Use this workflow for any code, dependency, documentation, or dashboard change. Keep changes focused and evidence-based.

## Procedure

1. Read `AGENTS.md` and the relevant source/tests before editing.
2. Check `git status --short --branch`, identify the base branch, and preserve unrelated work.
3. Make the smallest change that satisfies the request. Do not add credentials, personal paths, generated screenshots, or unrelated dependencies.
4. Add or update tests for behavior changes. Preserve public APIs, MCP tool contracts, and SSE event shapes.
5. Run verification from the repository root:
   - `uv lock --check`
   - `.venv/bin/pytest -q` or `uv run pytest -q`
   - `npm ci` and `npm test -- --run` from `dashboard/` when frontend files change
   - `npm run build` from `dashboard/` when frontend or packaged assets change
   - `git diff --check`
6. For dashboard changes, verify loopback default, token protection for non-loopback deployment, mobile/desktop behavior, and no horizontal overflow when browser tooling is available.
7. Review the final diff and confirm every changed file is intentional.
8. Commit with a Conventional Commit message, push to GitHub `origin`, and report the commit, branch, tests, and any blockers. Create a PR instead of pushing directly to `main` when repository policy requires it.

## Security checklist

- Dashboard defaults to loopback.
- Tokenless non-loopback dashboard binds are documented as trusted-LAN-only.
- Do not expose bearer tokens in logs, URLs, screenshots, README examples, or commits.
- Use TLS and network access controls for LAN deployments.
- Treat `.a2a-agents.json` and custom headers as secret-bearing configuration.

## Pitfalls

- A passing unit test is not proof that a live browser or deployment works.
- Do not claim a dependency upgrade is safe when tests fail after it.
- Do not commit `dashboard/node_modules`, `.venv`, caches, or screenshots.
- If a command fails because the environment lacks a tool, report the exact blocker and try a safe alternative.

## Verification

A contribution is ready only when the relevant tests/build/checks pass, the diff contains no accidental files, the security checklist is satisfied, and the final GitHub state is independently verified.
