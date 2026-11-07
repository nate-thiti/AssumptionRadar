# Spike 0001 — Do environment overrides take effect in a child Python process?

- **Date:** 2026-11-07
- **Script:** [`tools/spikes/spike_env.py`](../../tools/spikes/spike_env.py)
- **Run on:** Windows 11 (system locale Thai, `cp874`, UTC+7) · CPython 3.14.8 and 3.12.15 (identical results)
- **Not yet run on:** Linux, macOS (to be covered in CI — see open items)

## Method

For each scenario, spawn `sys.executable spike_env.py --probe` with the override applied
to the child's environment and compare observed values with the baseline child:
`time.localtime().tm_gmtoff`, `datetime.astimezone()` offset, `locale.getpreferredencoding()`,
default `open()` encoding, `locale.setlocale(LC_ALL, "")`, locale number formatting,
`time.strftime("%A")`.

## Results (Windows)

| Scenario | Effect in child | Verdict |
|---|---|---|
| `TZ=UTC` | offset 0 | works |
| `TZ=Asia/Tokyo` (IANA name) | offset **0**, not +9 | **silently wrong** — CRT parses as zone "Asi" with no offset |
| `TZ=JST-9` (POSIX) | offset +9 | works |
| `TZ=EST5EDT` (POSIX) | offset −5 | works |
| `TZ=UTC0` | offset 0 | works |
| `LANG=de_DE.UTF-8` | none | **no effect** |
| `LC_ALL=de_DE.UTF-8` | none | **no effect** |
| `LC_ALL=C` | none | **no effect** |
| `LC_ALL=German_Germany.1252` | none | **no effect** |
| `PYTHONUTF8=0` | none (already off) | n/a |
| `PYTHONUTF8=1` | preferred + `open()` encoding `cp874 → utf-8` | works |

## Conclusions

1. **Timezone verification on Windows must use POSIX-style `TZ` strings** (`JST-9`, `EST5EDT`, `UTC0`).
   IANA names must never be passed on Windows: they produce UTC without any error, which would make
   a verification report claim it tested Tokyo when it tested UTC. The scenario layer must map
   scenarios to per-platform `TZ` values and *verify inside the child* that the expected offset
   was actually applied before trusting the result (otherwise result = `inconclusive`).
2. **Locale cannot be changed through environment variables on Windows.** A locale scenario must
   instead be applied inside the child by the AssumptionRadar pytest plugin calling
   `locale.setlocale()` before the test session, and must report `unsupported` if the requested
   locale is not installed. On POSIX, `LC_ALL` is expected to work (to be confirmed in CI).
3. **Text-encoding is a high-value scenario on Windows.** A non-UTF-8 system code page (here `cp874`)
   vs `PYTHONUTF8=1` changes the default encoding of `open()`; code that omits `encoding=` behaves
   differently. Add an `encoding` scenario to v0.1.
4. General rule adopted for all scenarios: **a scenario is only trusted if the child confirms the
   condition took effect** (self-check probe). This is recorded in ADR-0003.

## Results on Linux and macOS (GitHub Actions, CPython 3.14.10, 2026-11-07)

Runners: `ubuntu-latest` (baseline UTC, `C.UTF-8`, UTF-8) and `macos-latest` (baseline UTC,
`en_US.UTF-8`). Windows runner (UTC, `cp1252`) matched the local Windows results above.

| Scenario | Linux | macOS |
|---|---|---|
| `TZ=Asia/Tokyo` (IANA) | +9, works | +9, works |
| `TZ=JST-9` / `TZ=EST5EDT` (POSIX) | works | works |
| `LANG` / `LC_ALL=de_DE.UTF-8` | **locale not installed** on the runner (`unsupported locale setting`) | works (`1.234.567,50`) |
| `LC_ALL=C` | setlocale `C`; encoding stays UTF-8 (PEP 538 coercion) | encoding switches to `utf-8` |
| `PYTHONUTF8=0` | encoding reported `UTF-8` (locale is UTF-8 anyway) | no effect |
| `PYTHONUTF8=1` | no effect (already UTF-8) | works |

Additional conclusions:

5. POSIX `TZ` strings work on all three platforms, confirming the choice in ADR-0003.
6. Locale availability depends on what the machine has installed (minimal Linux images often
   have only `C`/`C.UTF-8`); the self-check correctly reports such scenarios as `unsupported`.
7. On UTF-8 hosts the legacy-encoding scenario needs `PYTHONUTF8=0`, `PYTHONCOERCECLOCALE=0`
   and `LC_ALL=C` together; `LC_ALL=C` alone is undone by locale coercion.

### End-to-end scenario verdicts (CI run 42355608375, CPython 3.14.10)

A fixture test that depends on the condition (default-encoding read; locale number format):

| Scenario | Linux | macOS | Windows |
|---|---|---|---|
| encoding (`legacy` on UTF-8 hosts, `utf-8` on Windows) | regression | regression | regression |
| `locale:de_DE` / `fr_FR` / `tr_TR` | **unsupported** (locales not installed) | regression | regression |

Every verdict is either a real regression or an honest `unsupported`; none is a false
`no_difference`.

## Open items

- None for the spike itself. Per-platform scenario verdicts are printed in every CI run by
  `tools/diagnostics/scenario_verdicts.py`.
