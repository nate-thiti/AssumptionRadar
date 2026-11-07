# ADR-0003 — Controlled verification re-runs tests in a child process

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Verification must show that a test's outcome changes when one environmental condition changes,
without altering the parent process or the host.

## Decision

- Each verification is: **baseline run** + **modified run(s)** of the same pytest node ID, each in a
  fresh child process: `[sys.executable, "-m", "pytest", <nodeid>, "-p", "no:cacheprovider", ...]`
  via `subprocess.run` with an argument list (never `shell=True`), a timeout, and a cap on captured
  output.
- The child receives a fully constructed environment dict; the parent's `os.environ` and working
  directory are never mutated.
- The baseline is run `N` times (default 2). If baseline outcomes disagree → `inconclusive (flaky)`.
  If the baseline fails → `inconclusive (baseline failing)`.
- **Self-check:** in the child, the AssumptionRadar plugin applies in-process parts of a scenario
  (e.g. `locale.setlocale`) and writes a probe of the effective condition (UTC offset, preferred
  encoding, locale, cwd). The parent compares the probe with what the scenario intended. If the
  condition did not take effect → `unsupported` / `inconclusive`, never `no_difference`.
  This rule exists because of Spike 0001 (IANA `TZ` names silently become UTC on Windows).
- Scenario definitions are per-platform (POSIX `TZ` strings on Windows; locale via `setlocale`).
- Outcomes: `regression` (baseline passes, modified fails), `no_difference`, `inconclusive`,
  `unsupported`, `error` (runner failure / timeout). Report always includes both runs' outcome,
  exception summary (redacted), duration, and limitations.
- `env-unset` removes variables by name only; values are never logged.
- `cwd` runs the child in a fresh empty temporary directory; the test path is passed absolute.

## Security boundary

A child process is **not a sandbox**: the test code runs with the user's privileges and can touch
anything the user can. Verification only runs tests the user explicitly selects, in the user's own
project. Running untrusted projects requires an OS/container boundary (deferred; Docker is
available on the dev machine for a later opt-in mode).

## Consequences

- Verification cost ≈ (N baseline + scenarios) × test runtime; tests must be selected, not the whole
  suite by default.
- `no_difference` means "this scenario did not change the outcome", never "the test is
  environment-independent".
