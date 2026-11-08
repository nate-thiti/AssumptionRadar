# Limitations

AssumptionRadar reports evidence. It is deliberately careful about what that
evidence does and does not show.

## What a verdict means

- **`regression` is reproduced, not inferred.** The test passed in two
  unchanged re-runs and failed when one condition changed. It is still one
  condition at a time: a test that only breaks when two conditions change
  together is not found.
- **`no_difference` is not a guarantee.** It means this scenario did not change
  this test's outcome in this run. The code may still depend on the condition
  in a path the test does not exercise, or in a way the scenario does not
  change (another time zone, another locale).
- **Only the tests you run are checked**, one at a time, each in a fresh
  process. Interactions between tests (shared state, test order) are not
  covered.
- A flaky or failing baseline gives `inconclusive` for every scenario of that
  test rather than a guess.

## Scenarios

- `tz` uses POSIX `TZ` strings (`JST-9`, `EST5EDT`, `UTC0`) because Windows
  silently ignores IANA names such as `Asia/Tokyo`. Daylight-saving rules are
  not exercised.
- `locale:<name>` needs the locale to be installed. Minimal Linux images and
  many CI runners only have `C` and `C.UTF-8`; the scenario is then reported as
  `unsupported`.
- `encoding` on a UTF-8 machine sets `PYTHONUTF8=0`, `PYTHONCOERCECLOCALE=0`
  and `LC_ALL=C` together. `LC_ALL=C` also changes other locale-dependent
  behaviour in the re-run.
- `env-unset:NAME` is `unsupported` when `NAME` is not set to begin with.
- There is no scenario that removes programs from `PATH` yet. A program found
  through `PATH` (`subprocess.run(["git", ...])`) is only reported by
  observation.
- Verification is slow by design: each verified test runs at least
  `2 + number of scenarios` times. `--assumption-verify-max` (default 20) caps
  the number of tests.

## Observation

- Observation uses CPython audit hooks (PEP 578). It sees what Python reports:
  `open`, directory listing and changes, `os.chdir`, environment writes, DNS
  lookups, socket connects, `subprocess`, `os.system` and `os.spawn*`. Work
  done inside C extensions without going through these APIs is not seen.
- Environment variable **reads** have no audit event. With
  `--assumption-env-reads`, reads through `os.environ` (including
  `os.getenv`) are recorded by temporarily wrapping `os.environ`; reads from C
  code (`getenv()` in an extension) are not.
- Only events caused by project code are kept. An event is attributed to the
  innermost frame that belongs to your project, so a file opened by a library
  on your behalf is attributed to your call site. Imports are ignored.
- At most 10,000 events are kept per session; later ones are counted as
  dropped.
- Observation is in-process. It does not see what child processes started by
  your tests do.

## Not supported yet

- pytest-xdist (`-n`): rejected with a usage error when an
  `--assumption-*` option is used.
- Static analysis of source code (rules such as `AR-CWD-001`) is planned for
  v0.2; v0.1 only has runtime observation and verification.
- Python older than 3.11.

See also the [Security model](security.md).
