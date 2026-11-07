# ADR-0002 — Runtime observation uses PEP 578 audit hooks

- **Status:** Accepted
- **Date:** 2026-11-07

## Context

Runtime observation must record file, network, subprocess and working-directory operations
performed by code under test, attribute them to source locations, and not change behavior.
Options considered:

1. Monkey-patching `builtins.open`, `socket.socket`, `subprocess.Popen`, … — broad, but fragile,
   easy to leak between tests, misses C-level calls that bypass the patched names.
2. `sys.settrace` / `sys.monitoring` — sees every call; very high overhead, interferes with
   coverage and debuggers.
3. **`sys.addaudithook` (PEP 578)** — CPython raises documented audit events from C code for
   `open`, `os.listdir`, `os.mkdir`, `os.remove`, `os.rename`, `os.chdir`, `os.putenv`,
   `os.unsetenv`, `socket.connect`, `socket.getaddrinfo`, `subprocess.Popen`, `os.system`, etc.
   Low overhead; no patching.

## Decision

Use audit hooks as the primary mechanism.

- Audit hooks **cannot be removed** once added. The hook is installed once per process and is a
  no-op unless a `ContextVar`-scoped recorder is active, so nothing is recorded outside an
  explicit observation window and nothing leaks between tests.
- A re-entrancy guard prevents the hook from observing its own work.
- Events go into a bounded buffer; overflow is counted and reported, never silently dropped.
- Source attribution walks the caller frames and skips frames belonging to the stdlib and to
  AssumptionRadar itself.
- The hook never raises; any internal error is recorded as an observer error.

Environment-variable *reads* have no audit event. They are covered by an **opt-in** wrapper
around `os.environ` that records variable names only (never values) and is restored after each
test, including on exceptions. This is documented as the one place we wrap a global object.

## Consequences

- Not visible: operations performed by native extensions that bypass CPython's audited APIs,
  child processes (we only see that they were spawned), and other interpreters. Documented as
  limitations.
- CPython-specific; PyPy is not supported for observation.
