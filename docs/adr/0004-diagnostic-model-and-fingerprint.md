# ADR-0004 — Diagnostic model and stable fingerprints

- **Status:** Accepted
- **Date:** 2026-11-07

## Decision

Immutable (`frozen=True, slots=True`) dataclasses in `assumptionradar.core.models`:

- `Finding`: `rule_id`, `title`, `message`, `category`, `severity`, `confidence`,
  `location: SourceLocation | None`, `evidence: tuple[Evidence, ...]`, `verification:
  VerificationStatus`, `remediation`, `failure_scenario`, `docs_url`, `test_id: str | None`,
  `fingerprint`, `metadata: Mapping[str, str]` (read-only).
- `Evidence`: `kind ∈ {observed, inferred, reproduced}`, `method` (e.g. `audit-hook`, `ast`,
  `verification`), `detail` (redacted text), `location`.
- `Severity ∈ {info, warning, error}`, `Confidence ∈ {low, medium, high}`,
  `VerificationStatus ∈ {not_verified, regression, no_difference, inconclusive, unsupported, error}`.
  These three axes are independent: observing a file read is high-confidence evidence of a
  dependency, not a high-severity bug.
- `SourceLocation`: `path` (POSIX-style, relative to project root), `line`, `column`, `symbol`.

**Fingerprint** = first 16 hex chars of SHA-256 over
`rule_id | relative path | enclosing symbol | normalized key` where the normalized key is
rule-specific (e.g. the accessed relative path, the env var name, the scenario name) and
**excludes line numbers**, so findings survive unrelated edits. Collisions within one report are
disambiguated with a `:n` suffix in stable order.

**Ordering:** reports sort by `(path, line, column, rule_id, fingerprint)`.

**Report schema:** JSON with `"schema_version": "1"`; additive changes only within a major
version.

## Consequences

- Baselines (v0.2) can rely on fingerprints.
- Moving code to another function or file creates a new fingerprint (intended).
