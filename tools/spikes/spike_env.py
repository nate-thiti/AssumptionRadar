"""P0-3 spike: do TZ / locale env overrides take effect in a child Python process?

Usage: python spike_env.py            -> runs all scenarios, prints a table
       python spike_env.py --probe    -> (child mode) prints observed values as JSON
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

SCENARIOS: dict[str, dict[str, str | None]] = {
    "baseline": {},
    "TZ=UTC": {"TZ": "UTC"},
    "TZ=Asia/Tokyo": {"TZ": "Asia/Tokyo"},
    "TZ=JST-9": {"TZ": "JST-9"},
    "TZ=EST5EDT": {"TZ": "EST5EDT"},
    "TZ=UTC0": {"TZ": "UTC0"},
    "LANG=de_DE.UTF-8": {"LANG": "de_DE.UTF-8"},
    "LC_ALL=de_DE.UTF-8": {"LC_ALL": "de_DE.UTF-8"},
    "LC_ALL=C": {"LC_ALL": "C"},
    "LC_ALL=German_Germany.1252": {"LC_ALL": "German_Germany.1252"},
    "PYTHONUTF8=0": {"PYTHONUTF8": "0"},
    "PYTHONUTF8=1": {"PYTHONUTF8": "1"},
}


def probe() -> dict[str, object]:
    import datetime
    import locale
    import time

    out: dict[str, object] = {}
    lt = time.localtime(1_700_000_000)
    out["tm_gmtoff"] = lt.tm_gmtoff
    out["tzname"] = list(time.tzname)
    out["astimezone_offset"] = str(
        datetime.datetime.fromtimestamp(1_700_000_000).astimezone().utcoffset()
    )
    out["preferred_encoding"] = locale.getpreferredencoding(False)
    out["fs_encoding"] = sys.getfilesystemencoding()
    out["open_default_encoding"] = open(os.devnull).encoding  # noqa: SIM115
    try:
        out["setlocale_empty"] = locale.setlocale(locale.LC_ALL, "")
    except locale.Error as exc:
        out["setlocale_empty"] = f"ERROR {exc}"
    out["number_fmt"] = locale.format_string("%.2f", 1234567.5, grouping=True)
    out["weekday"] = time.strftime("%A", time.localtime(1_700_000_000))
    out["str_upper_i"] = "i".upper()
    return out


def main() -> None:
    if "--probe" in sys.argv:
        print(json.dumps(probe()))
        return
    base_env = {k: v for k, v in os.environ.items() if k not in {"TZ", "LANG", "LC_ALL", "PYTHONUTF8"}}
    results: dict[str, dict[str, object]] = {}
    for name, overrides in SCENARIOS.items():
        env = dict(base_env)
        for k, v in overrides.items():
            if v is not None:
                env[k] = v
        cp = subprocess.run(
            [sys.executable, __file__, "--probe"],
            env=env, capture_output=True, text=True, timeout=30, check=False,
        )
        if cp.returncode != 0:
            results[name] = {"error": cp.stderr.strip()[-300:]}
        else:
            results[name] = json.loads(cp.stdout)
    base = results["baseline"]
    print(f"python {sys.version.split()[0]} on {sys.platform}")
    print(f"baseline: {json.dumps(base)}")
    for name, res in results.items():
        if name == "baseline":
            continue
        diff = {k: v for k, v in res.items() if base.get(k) != v}
        print(f"{name:28} -> {json.dumps(diff) if diff else 'NO EFFECT'}")


if __name__ == "__main__":
    main()
