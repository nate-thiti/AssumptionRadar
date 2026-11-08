# Security model

## Re-runs are not a sandbox

`--assumption-verify` runs your tests **again**, in child processes, with your
user's privileges, on your machine or CI runner. Each verified test runs at
least two more times, plus once per scenario.

- Anything a test does, it does again: writing to a database, calling an API,
  deleting files, sending messages.
- The child process has the same access to the network and filesystem as the
  pytest run that started it. Only the scenario's own condition changes (an
  environment variable, the working directory, the temporary directory, the
  locale).
- **Do not run verification on code you would not run yourself.** Treat it like
  running the test suite several times.

What is isolated:

- The parent pytest process is not changed: its environment, working
  directory, time zone, locale and temporary directory stay the same. This is
  checked by `tests/security`.
- Verification children do not start verification themselves, even when
  options arrive through `PYTEST_ADDOPTS`.
- Each re-run has a timeout (`--assumption-timeout`, default 120 seconds). On
  timeout the child process is killed and the scenario is `inconclusive`.
  Processes that the test itself starts are not tracked and may outlive it.

## What is kept out of reports

Reports and terminal output are meant to be safe to keep as CI artifacts.

- **Environment variable values are never recorded.** Observation records
  variable names only.
- Child output kept in the report is cut to the last 4,000 characters and
  redacted before it is stored.
- Redaction masks:
  - private key blocks (`-----BEGIN ... PRIVATE KEY-----`), even when truncated
  - credentials in URLs (`https://user:password@host`)
  - `Bearer` tokens
  - values assigned to keys ending in `password`, `secret`, `token`,
    `api_key`, `access_key`, `credentials` and similar (`DB_PASSWORD=...`,
    `"api_key": "..."`)
  - common token formats: GitHub, AWS access key ids, Slack, `sk-...` keys,
    JWTs
  - the values (8 characters or longer) of environment variables whose names
    contain `TOKEN`, `PASSWORD`, `PASSWD`, `SECRET`, `CREDENTIAL` or `APIKEY`,
    or end in `_KEY`, wherever they appear, also in file paths and
    assertion messages
- Your home directory is replaced by `~`.

What redaction cannot catch:

- Secrets that do not come from such an environment variable and do not match
  a pattern, for example a password read from a file and printed as is.
- Very short secret values (under 8 characters for environment values).
- Secrets inside test names or parameter ids, which appear in test ids.

Keep reports where you keep test logs, not in public places, unless you have
looked at them.

## Reporting a vulnerability

Please do not post vulnerability details in a public issue. Open an issue on
[nate-thiti/AssumptionRadar](https://github.com/nate-thiti/AssumptionRadar/issues)
asking for a private contact, without details, and the maintainer will reply.
