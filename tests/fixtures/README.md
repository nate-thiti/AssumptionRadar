# Fixture projects for end-to-end tests

These are tiny pytest projects run by `tests/e2e/test_fixture_projects.py`.
They are not collected by the main test suite (see `collect_ignore` in
`tests/conftest.py`).

- `positive/` passes on the author's machine but hides one environment
  assumption per test: a relative path, local time, a required environment
  variable, a program found through `PATH`, and the default text encoding.
- `negative/` does the same jobs portably; nothing should be reported.

The end-to-end test copies each project to a temporary directory, runs pytest
with `--assumption-observe --assumption-verify=...`, and compares the report
with the exact expected verdicts and flagged events: nothing missing, nothing
extra.
