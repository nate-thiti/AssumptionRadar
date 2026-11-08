# AssumptionRadar

Find the hidden environment assumptions in your test suite before they break
on someone else's machine.

A test that passes on your laptop can fail in CI, on Windows, in another time
zone or when run from a different folder. AssumptionRadar **re-runs your
passing tests under controlled changes to the environment** and tells you which
ones break, and it can **record what your code touches at runtime** (files,
environment variables, subprocesses, network).

Every reported regression is reproduced, not guessed: the test passed twice in
an unchanged environment and then failed when one condition changed.

> **Status:** pre-alpha (v0.1 in development). Not yet on PyPI. Tested on
> Windows, macOS and Linux with Python 3.11 to 3.14.

## Install

From GitHub until the first release is published:

<!-- doctest: skip needs network access; the CI package job installs the built wheel instead -->
```bash
pip install "assumptionradar @ git+https://github.com/nate-thiti/AssumptionRadar"
```

AssumptionRadar has no runtime dependencies. Its pytest plugin uses the pytest
already installed in your project, and registers itself automatically.

## A 30-second example

A test that only passes when pytest is started from the project folder:

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

Re-run the passing tests from an empty working directory:

<!-- doctest: run -->
```bash
pytest --assumption-verify=cwd
```

<!-- doctest: output -->
```text
verification: 1 regression, 1 no_difference (2 tests)
  REGRESSION    tests/test_settings.py::test_settings
      cwd: regression (reproduced) - test passed in the baseline but failed under this scenario
        fix: build paths from Path(__file__).parent or a fixture instead of the working directory
  NO_DIFFERENCE tests/test_settings.py::test_settings_portable
```

Both tests pass in a normal run, so pytest alone would never tell you.

## What it checks

| Scenario | What changes in the re-run | Typical hidden assumption |
|---|---|---|
| `cwd` | empty temporary working directory | `open("data.json")` relative to where pytest was started |
| `tmpdir` | temp directory path with spaces and non-ASCII characters | unquoted paths, ASCII-only paths |
| `tz`, `tz:+9`, `tz:-5`, `tz:utc` | local time zone | `datetime.now()` / naive datetimes treated as a fixed zone |
| `encoding` | default text encoding flipped | `open()` without `encoding=` |
| `locale:de_DE`, `fr_FR`, `tr_TR` | locale | locale-dependent number and date formatting |
| `env-unset:NAME` | one environment variable removed | `os.environ["NAME"]` with no default |

`default` means `cwd, tz, encoding, tmpdir`. Run `assumptionradar scenarios`
and `assumptionradar explain <name>` for details.

## Usage

pytest options (all opt-in; without them the plugin does nothing):

| Option | Meaning |
|---|---|
| `--assumption-verify=SPEC` | re-run passing tests under scenarios, e.g. `default` or `cwd,tz,env-unset:API_URL` |
| `--assumption-verify-max=N` | verify at most N passing tests (default 20) |
| `--assumption-timeout=SECONDS` | timeout for each re-run (default 120) |
| `--assumption-observe` | record files, environment writes, subprocesses and network use by project code |
| `--assumption-env-reads` | also record environment variable reads (names only) |
| `--assumption-report=PATH` | write a JSON report ([schema](src/assumptionradar/reporting/schemas/report-v1.json)) |
| `--assumption-strict` | fail the run when a regression is found (for CI) |

Command line:

| Command | Meaning |
|---|---|
| `assumptionradar scenarios` | list scenarios |
| `assumptionradar explain NAME` | explain one scenario |
| `assumptionradar report show PATH` | print a saved report; `--format json` for machine-readable output |

Exit codes of `report show`: `0` nothing found, `1` regression, `2` bad input,
`3` internal error, `4` inconclusive. Add `--exit-zero` to always exit `0`.

## Learn more

- [Quickstart](docs/quickstart.md): a full walk-through, including CI
- [Limitations](docs/limitations.md): what AssumptionRadar does **not** prove
- [Security model](docs/security.md): the re-runs are **not a sandbox**
- [Design decisions](docs/adr) and [ROADMAP](ROADMAP.md)

## Development

<!-- doctest: skip development setup; CI runs the same steps -->
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # bin/python on macOS and Linux
.venv/Scripts/python -m pytest
```

Every code block in this README and in `docs/` is executed by
`tests/docs/test_doc_examples.py`.

## License

MIT
