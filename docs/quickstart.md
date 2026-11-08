# Quickstart

This walk-through uses a tiny project with one hidden assumption. Every
command and output below is executed by the test suite
(`tests/docs/test_doc_examples.py`), so it matches what you will see. Lines
shown as `...` stand for output that differs per machine (paths, counts).

## 1. A project with a hidden assumption

<!-- doctest: file settings.json -->
```json
{"debug": false}
```

<!-- doctest: file tests/test_settings.py -->
```python
import json
from pathlib import Path


def test_settings():
    with open("settings.json", encoding="utf-8") as fh:
        assert json.load(fh)["debug"] is False


def test_settings_portable():
    path = Path(__file__).parent.parent / "settings.json"
    assert json.loads(path.read_text(encoding="utf-8"))["debug"] is False
```

`test_settings` opens `settings.json` relative to the **current working
directory**. It passes when you run `pytest` from the project folder, and fails
when an IDE, a CI step or a monorepo runner starts pytest somewhere else.
`test_settings_portable` builds the path from the test file instead.

A normal run passes and shows nothing unusual:

<!-- doctest: run -->
```bash
pytest -q
```

<!-- doctest: output -->
```text
2 passed
```

## 2. See what the tests touch

`--assumption-observe` records what project code does at runtime, using
Python's audit hooks. It never changes the outcome of a test.

<!-- doctest: run -->
```bash
pytest --assumption-observe
```

<!-- doctest: output -->
```text
observation: 2 events (file_open: 2)
  ! file_open settings.json [mode=r, relative=true] at tests/test_settings.py:6 (tests/test_settings.py::test_settings)
...
```

Lines marked `!` are likely assumptions: a relative path, a program looked up
on `PATH`, or (with `--assumption-env-reads`) a missing environment variable.
Observation is evidence of what happened, not proof that something breaks.

## 3. Prove it breaks

`--assumption-verify` re-runs each passing test in a separate process, first
twice unchanged (the baseline), then once per scenario:

<!-- doctest: run -->
```bash
pytest --assumption-verify=cwd
```

<!-- doctest: output -->
```text
assumptionradar: verifying 2 test(s) under: cwd
...
verification: 1 regression, 1 no_difference (2 tests)
  REGRESSION    tests/test_settings.py::test_settings
      cwd: regression (reproduced) - test passed in the baseline but failed under this scenario
        fix: build paths from Path(__file__).parent or a fixture instead of the working directory
  NO_DIFFERENCE tests/test_settings.py::test_settings_portable
```

Possible verdicts:

| Verdict | Meaning |
|---|---|
| `regression` | passed in the baseline, failed under the scenario: reproduced |
| `no_difference` | the scenario did not change the outcome (this is **not** proof of independence) |
| `unsupported` | the condition could not be applied here, e.g. the locale is not installed |
| `inconclusive` | the run timed out or the baseline was flaky |
| `error` | the test could not be run, e.g. it was not found |

A scenario only counts if the re-run confirms that the condition actually took
effect. A time zone that the platform silently ignored is reported as
`unsupported`, never as `no_difference`.

To try several conditions at once, use `default` (cwd, tz, encoding, tmpdir) or
a list:

<!-- doctest: run -->
```bash
pytest -q --assumption-verify=default,env-unset:HOME --assumption-verify-max=5
```

Learn what a scenario does:

<!-- doctest: run -->
```bash
assumptionradar explain cwd
```

<!-- doctest: output -->
```text
cwd
  use:     cwd
  changes: Run the test from an empty temporary working directory.
...
```

## 4. Save a report

<!-- doctest: run -->
```bash
pytest -q --assumption-observe --assumption-verify=cwd --assumption-report=report.json
```

The report is JSON ([schema v1](../src/assumptionradar/reporting/schemas/report-v1.json)).
Show it again later, or in another CI step:

<!-- doctest: run exit=1 -->
```bash
assumptionradar report show report.json
```

<!-- doctest: output -->
```text
verification: 1 regression, 1 no_difference (2 tests)
  REGRESSION    tests/test_settings.py::test_settings
...
```

`report show` exits `1` because the report contains a regression. Use
`--format json` for machine-readable output (stdout then contains only JSON),
and `--exit-zero` when you only want to print it.

## 5. Use it in CI

By default AssumptionRadar never changes pytest's exit status. Add
`--assumption-strict` to fail the run when a regression is found:

<!-- doctest: run exit=1 -->
```bash
pytest -q --assumption-verify=cwd --assumption-strict
```

A GitHub Actions step could look like this:

<!-- doctest: skip CI configuration example, not a command -->
```yaml
- name: Check for hidden environment assumptions
  run: pytest --assumption-verify=default --assumption-strict --assumption-report=assumptions.json
```

Verification re-runs each selected test several times, so start with a small
`--assumption-verify-max` on large suites. See [Limitations](limitations.md)
and the [Security model](security.md) before running it on code you do not
trust.
