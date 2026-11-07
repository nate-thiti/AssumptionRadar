# AssumptionRadar — แผนย่อยงาน (Roadmap)

> **เป้าหมาย:** เครื่องมือ Python ที่หา *สมมติฐานด้านสภาพแวดล้อมที่ซ่อนอยู่* (cwd, env var, timezone, locale, ไฟล์, network, executable ฯลฯ) และ **พิสูจน์ด้วยหลักฐาน** ว่าโค้ดพังเมื่อสภาพแวดล้อมเปลี่ยน
>
> **จุดขายหลัก (ต้องทำให้ดีที่สุดก่อน):** Controlled Verification คือรันเทสซ้ำใน child process ภายใต้เงื่อนไขที่เปลี่ยน แล้วรายงานว่า "พังจริง" พร้อมหลักฐาน
> **ไม่ใช่จุดขาย:** static lint ทั่วไปที่ ruff, pytest-socket หรือ pytest-randomly ทำได้อยู่แล้ว

---

## 0. กติกาการแตกงาน

| ชั้น | ใช้กับ | หมายเหตุ |
|---|---|---|
| **Opus** | ADR, ออกแบบ data model/contract, เขียนเทส+เฉลยก่อน, ตัดสิน false positive, review | ไม่พิมพ์โค้ดยาวเอง |
| **Haiku** (`haiku-coder`) | implement ให้ผ่านเทสที่ Opus เขียนไว้แล้ว · ไฟล์ระบุครบ · ≤30K token | ตกตัวตรวจ 1 ครั้ง → ส่ง Sonnet |
| **Sonnet** (`sonnet-hands`) | งานที่ต้องสำรวจ/รันข้ามสภาพแวดล้อม/ดีบัก CI/ตรวจด้วยตา/browser | สำรองเมื่อ Haiku ตก |

**รูปแบบใบงาน:** ทุก task มี `ID` · ชั้น · ไฟล์ที่แตะ · **เกณฑ์รับงาน (ตรวจได้อัตโนมัติ)** · task ที่ต้องเสร็จก่อน
**Definition of Done ทุก task:** `ruff check` + `ruff format --check` + `mypy --strict src` + `pytest` ผ่านทั้งหมด

---

## Phase 0 — ตั้งต้นและตัดสินใจ

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P0-1 | เช็คชื่อ `assumptionradar` บน PyPI และ GitHub ว่าว่างไหม (รวมชื่อคล้ายกัน) | Haiku (`web-reader`) | รายงานสถานะ + URL ที่ตรวจ | – |
| P0-2 | เช็ค Python ที่มีในเครื่อง (`py -0`) และเลือกช่วงเวอร์ชันที่รองรับ (เสนอ 3.11–3.13) | Haiku | ตารางเวอร์ชันที่ติดตั้งจริง | – |
| P0-3 | **Spike:** ทดลองว่าการเปลี่ยน `TZ` / `LANG` / `LC_ALL` ใน child process มีผลจริงไหมบน Windows (Windows ไม่มี `time.tzset` และ CRT รับแค่ TZ แบบ POSIX เช่น `JST-9` ไม่รับชื่อ IANA) | Opus เขียนสคริปต์ทดลอง → Haiku รัน | ตารางผล: scenario × OS → ได้ผล/ไม่ได้ผล | P0-2 |
| P0-4 | เขียน ADR-001 ขอบเขต v0.1 · ADR-002 runtime observation ใช้ `sys.addaudithook` (PEP 578) · ADR-003 verification = รัน pytest ซ้ำใน child process (**ไม่ใช่ sandbox**) · ADR-004 diagnostic model + fingerprint | Opus | `docs/adr/0001..0004-*.md` | P0-3 |
| P0-5 | Repo skeleton: `git init`, `src/` layout, `pyproject.toml` (hatchling), dev extras (`pytest`, `pytest-cov`, `ruff`, `mypy`, `build`, `twine`, `jsonschema`), `pre-commit`, `.gitignore`, LICENSE (MIT/Apache — **ผู้ใช้เลือก**) | Haiku (สเปกจาก Opus) | `pip install -e .[dev]` และ ruff/mypy/pytest exit 0 | P0-4 |

**ผล Phase 0 (2026-11-07):**
- P0-1 ✅ ชื่อ `assumptionradar` (รวม `assumption-radar` และ `assumptionsradar`) ยังไม่มีบน PyPI (ได้ HTTP 404) และไม่พบ repo ชื่อนี้บน GitHub
- P0-2 ✅ ในเครื่องมี CPython 3.14.8 และ 3.12.15 · ส่วน 3.11 และ 3.13 จะทดสอบใน CI · มี Docker 29.8 สำหรับโหมด container ในอนาคต
- P0-3 ✅ ดู [docs/spikes/0001-env-overrides.md](docs/spikes/0001-env-overrides.md)
- P0-4 ✅ [docs/adr/](docs/adr/) 0001–0004
- P0-5 ✅ skeleton: ruff/mypy strict/pytest ผ่าน, build wheel+sdist ผ่าน `twine check --strict`, `assumptionradar --version` ทำงาน (venv Python 3.14, ruff 0.17.4, mypy 2.4.1, pytest 9.2.0)
- License: MIT · Author: SomNate · GitHub: https://github.com/nate-thiti/AssumptionRadar (public, 2026-11-07)

---

## Phase 1 — v0.1 "Verify" (หัวใจของโปรเจค)

**ผลลัพธ์:** `pytest --assumption-verify=cwd,tz,env` บอกได้ว่าเทสไหนพังภายใต้เงื่อนไขไหน พร้อมหลักฐาน และ `--assumption-observe` บอกว่าเทสแตะไฟล์ network หรือ subprocess อะไรบ้าง

### 1A. Core model

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P1-1 | ออกแบบ `core/models.py`: `Finding`, `Evidence(kind=observed\|inferred\|reproduced)`, `Severity`, `Confidence`, `VerificationStatus(regression\|no_difference\|inconclusive\|error)`, `SourceLocation` (frozen dataclass) + **เขียนเทสก่อน** | Opus | `tests/unit/test_models.py` เขียนเสร็จ (ยังแดง) | P0-5 |
| P1-2 | Implement models ให้ผ่านเทส P1-1 | Haiku | เทส P1-1 เขียวทั้งหมด | P1-1 |
| P1-3 | Fingerprint: sha256 ของ `rule_id + relpath + symbol + normalized snippet` (**ไม่ใช้เลขบรรทัด** เพื่อไม่ให้เพี้ยนเมื่อมีคนเพิ่มบรรทัด) — Opus เขียนเทส · Haiku implement | Opus → Haiku | เทสยืนยันว่า fingerprint คงเดิมเมื่อเลื่อนบรรทัด และเปลี่ยนเมื่อแก้โค้ด | P1-2 |
| P1-4 | `utils/redact.py`: ห้ามเก็บค่า env var (เก็บแค่ชื่อ), แทน home dir ด้วย `~`, ตัดสตริงที่ยาวเกินกำหนด, mask pattern ที่หน้าตาเป็น token — Opus เขียนเทส · Haiku implement | Opus → Haiku | เทสยิง secret ปลอม 10 แบบแล้วไม่มีค่าใดหลุดออกมา | P1-2 |

**สถานะ 1A (2026-11-07):** ✅ P1-1 ถึง P1-4 เสร็จ · 126 tests ผ่าน · coverage ของ models และ fingerprint 100% ส่วน redact 94% (branch)

### 1B. Verification engine

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P1-5 | ออกแบบ `Scenario` (ชื่อ, env overrides, env removals, cwd, timeout) และ `VerificationResult` (baseline, modified, diff, exception, timeout, limitations) + เขียนเทส | Opus | เทสพร้อม | P1-2 |
| P1-6 | `analysis/verification.py`: รัน `sys.executable -m pytest <nodeid> -p no:cacheprovider -q` ด้วย `subprocess.run` (ไม่ใช้ `shell=True`) · timeout · จำกัดขนาด output · รัน baseline ก่อน ถ้า baseline fail → `inconclusive` · รัน baseline ซ้ำ N ครั้งเพื่อจับเทส flaky | Haiku | เทส P1-5 เขียว · parent process `os.environ` และ `os.getcwd()` ไม่เปลี่ยน (assert ในเทส) | P1-5 |
| P1-7 | Built-in scenarios: `cwd` (tmp dir ว่าง), `tz` (บน Windows ใช้ TZ แบบ POSIX เช่น `JST-9` เท่านั้น), `encoding` (`PYTHONUTF8=1` เทียบกับ code page ของระบบ), `locale` (บน Windows ตั้งผ่าน `setlocale` ใน child เพราะ env var ไม่มีผล), `env-unset:<NAME>`, `tmpdir` · **child ต้องตรวจเองว่าเงื่อนไขมีผลจริง** (ADR-0003) ถ้าไม่มีผลต้องคืน `unsupported` หรือ `inconclusive` **ห้ามคืน `no_difference`** | Opus สเปก → Haiku | เทส e2e: fixture ที่พังจริงได้ผล `regression` ส่วน fixture ที่ปลอดภัยได้ผล `no_difference` | P1-6, P0-3 |

**สถานะ 1B (2026-11-07):** ✅ P1-5 ถึง P1-7 เสร็จ · 273 tests ผ่าน (มี integration 24 ข้อที่รัน child process จริงบน Windows + Python 3.14) · ยังไม่ได้รันบน Linux/macOS · `_child.py` ยังไม่มีตัวเลข coverage เพราะรันใน subprocess (ต้องเปิด coverage แบบ subprocess ทีหลัง)
- บทเรียน: pytest คืน exit code 4 (ไม่ใช่ 2) เมื่อ import โมดูลเทสล้มเหลวและเรียกด้วย node id เจาะจง ตอนนี้ child จึงนับ collection error ผ่าน hook ของ pytest โดยตรง ไม่เดาจาก exit code

### 1C. Runtime observation

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P1-8 | ออกแบบ observer: `sys.addaudithook` สำหรับ event `open`, `os.listdir`, `os.mkdir`, `os.remove`, `os.rename`, `socket.connect`, `socket.getaddrinfo`, `subprocess.Popen`, `os.system`, `os.chdir`, `os.putenv`, `os.unsetenv` · **ถอด hook ไม่ได้** จึงต้องเปิด/ปิดผ่าน `ContextVar` · มี reentrancy guard · buffer แบบมีเพดาน · หา caller frame โดยข้าม frame ของ stdlib และของ radar เอง | Opus (ADR+เทส) | เทสพร้อม | P1-2 |
| P1-9 | Implement `analysis/runtime.py` ตาม P1-8 | Haiku | เทสเปิดไฟล์ด้วย relative path แล้ว event ระบุไฟล์+บรรทัดถูก · ไม่มี event รั่วข้ามเทส · overhead วัดได้และบันทึกไว้ | P1-8 |
| P1-10 | (opt-in) ติดตามการอ่าน env var ด้วย wrapper ครอบ `os.environ` เพราะไม่มี audit event ให้ · บันทึกแค่ชื่อตัวแปร · คืนค่าเดิมทุกครั้ง (รวมกรณีเกิด exception) | Opus → Haiku | เทสยืนยันว่า `os.environ` กลับเป็น object เดิมหลังจบ และไม่มีค่าตัวแปรใดในรายงาน | P1-9 |

**สถานะ 1C (2026-11-07):** ✅ P1-8 ถึง P1-10 เสร็จ · 312 tests ผ่าน · coverage ของ `runtime.py` 87% (branch)
- บทเรียน: CPython ปิดการ trace ภายใน audit hook โดยค่าเริ่มต้น จึงต้องตั้ง `__cantrace__` ใน `tests/conftest.py` เพื่อให้วัด coverage ได้ ส่วนโค้ดจริงคงค่าเดิม
- บทเรียน: บน Windows, event `subprocess.Popen` ส่ง args มาเป็น command line string ไม่ใช่ list
- ค้างไว้ทำทีหลัง: `redact_text` เรียก `Path.home()` ทุก event ควร cache ไว้ (วัดผลใน benchmark ก่อนแก้) · เทสของ `os.spawn` และ path แบบ bytes ยังไม่ครอบ

### 1D. pytest plugin + รายงาน

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P1-11 | `integrations/pytest_plugin.py`: `--assumption-observe`, `--assumption-verify=<scenarios>`, `--assumption-report=<path>`, `--assumption-rules`, `--assumption-strict` · entry point `pytest11` · **ค่าเริ่มต้นห้ามทำให้เทสที่เคยผ่านกลับ fail** · ถ้าใช้ร่วมกับ xdist (`-n`) ให้ error ชัดเจนว่ายังไม่รองรับ | Opus สเปก+เทส (`pytester`) → Haiku | เทส `pytester` เขียว | P1-7, P1-9 |
| P1-12 | `reporting/json.py` (schema version `"1"`, เรียงลำดับคงที่) + `docs/schema/report-v1.json` + เทส validate ด้วย jsonschema | Opus schema → Haiku | รายงานจากรันจริงผ่านการ validate | P1-11 |
| P1-13 | `reporting/terminal.py`: สรุปตาม severity, rule id, ตำแหน่ง `file:line`, ชนิดหลักฐาน, วิธีแก้ · รองรับ `NO_COLOR` | Haiku | snapshot test | P1-12 |
| P1-14 | CLI ขั้นต่ำ: `--version`, `rules`, `explain <RULE>`, `report show <json>` · กำหนด exit code (0 ผ่าน · 1 เจอ finding เกินเกณฑ์ · 2 config ผิด · 3 internal error · 4 verification inconclusive) · stdout ห้ามปน log เมื่อเลือก `--format json` | Haiku | เทส CLI ด้วย `subprocess` | P1-12 |

**สถานะ P1-11 (2026-11-07):** ✅ เสร็จ · 336 tests ผ่าน (pytester 24 ข้อ) · ruff และ mypy strict ผ่าน
- มี option `--assumption-observe`, `-env-reads`, `-verify`, `-verify-max`, `-timeout`, `-report`, `-strict` · ยังไม่มี `--assumption-rules` เพราะยังไม่มีชุดกฎ (มาพร้อม static scanner ใน Phase 2)
- แยกเป็นสองโมดูล: `pytest_plugin.py` (เบา โหลดในทุก pytest run มีแค่ option) และ `_pytest_session.py` (โหลดเฉพาะตอนผู้ใช้ใส่ option) · ยืนยันแล้วว่า import ตัวเบาไม่ดึงโมดูล analysis/reporting เข้ามา
- child process ของ verification ปิด plugin ตัวเองผ่าน `mark_verification_child()` จึงไม่วนซ้ำแม้ option มาทาง `PYTEST_ADDOPTS`
- แก้บั๊กระหว่างทาง: ตัวกรอง import ของ runtime เดิมตัดโค้ดระดับโมดูลของโปรเจคทิ้ง (เช่น `open()` ตอน import test file) · ตอนนี้ไล่ frame จนเจอไฟล์โปรเจคก่อนจึงนับ
- ยังไม่ได้ทำ: coverage ของโค้ดฝั่ง verification ใน `_pytest_session.py` (69%) ไม่ครบ เพราะรันใน subprocess ของ pytester · ค้างรวมกับเรื่องเปิด coverage ใน subprocess

### 1E. ทดสอบจริง + ปล่อย v0.1

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P1-15 | Fixture projects ใน `tests/fixtures/`: **positive** (อ่าน `config.json` แบบ relative, ฟอร์แมต `datetime.now()` ด้วย local tz, ต้องมี env var, เรียก `git` ผ่าน PATH) และ **negative** (`Path(__file__).parent`, ระบุ tz ชัดเจน, `monkeypatch.setenv`) | Opus | e2e test ยืนยันชุด finding ที่คาดไว้ **ตรงทุกตัว** (ไม่ขาดและไม่เกิน) | P1-11 |
| P1-16 | Security tests: secret ไม่หลุดเข้ารายงาน · parent env ไม่เปลี่ยน · timeout ทำงาน · output ถูกตัดตามเพดาน | Opus เทส → Haiku แก้ | เขียว | P1-15 |
| P1-17 | Docs v0.1: README, Quickstart, Limitations, Security model (ระบุชัดว่า child process **ไม่ใช่ sandbox**) | Opus เขียน | ตัวอย่างทุกบล็อกในเอกสารรันได้จริง (เช็คด้วยสคริปต์หรือ pytest ที่รัน code block) | P1-15 |
| P1-18 | Release check ในเครื่อง: `python -m build` → `twine check` → ติดตั้ง wheel ใน venv ใหม่ → smoke test CLI + plugin | Sonnet | log ผลทุกขั้น exit 0 | P1-17 |

---

## Phase 2 — v0.2 "Scan" (static analysis เฉพาะส่วนที่ ruff ไม่ทำ)

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P2-1 | AST engine: ค้นไฟล์ + include/exclude glob · `SyntaxError` กลายเป็น diagnostic (ไม่ crash) · ตาราง import alias (`from pathlib import Path as P`) · cache ตาม hash ของไฟล์ · จำกัดขนาดไฟล์ · **ห้าม import หรือรันโค้ดที่สแกน** | Opus ออกแบบ+เทส → Haiku | เทส alias, syntax error, unicode path, path ที่มีช่องว่าง · เทสยืนยันว่า fixture ที่มี side effect ตอน import ไม่ถูกรัน | P1-2 |
| P2-2 | `AR-CWD-001` relative path ที่ **ถูกเข้าถึงจริง** (`open`, `Path(..).read_text`, `json.load(open(..))`) ไม่ใช่แค่สร้าง path | Opus เทส → Haiku | fixture ทั้ง positive และ negative ตรงทุกตัว | P2-1 |
| P2-3 | `AR-ENV-001` อ่าน env var แบบไม่มี default (`os.environ["X"]`) · `AR-ENV-002` รวบรวมรายชื่อ env var ที่โค้ดใช้ เพื่อเทียบกับที่ประกาศไว้ | Opus → Haiku | เช่นเดียวกัน | P2-1 |
| P2-4 | `AR-EXE-001` subprocess ที่เรียกคำสั่งแบบไม่ระบุ path (พึ่ง PATH) · **ไม่รันคำสั่งเพื่อตรวจ** | Opus → Haiku | เช่นเดียวกัน | P2-1 |
| P2-5 | `AR-PLAT-001` hardcoded drive letter หรือตัวคั่น path · ใช้ API เฉพาะ Unix/Windows โดยไม่มี guard | Opus → Haiku | เช่นเดียวกัน | P2-1 |
| P2-6 | `AR-IMP-001` optional import (`try: import x except ImportError`) ที่ไม่ได้ประกาศใน extras | Opus → Haiku | เช่นเดียวกัน | P2-1 |
| P2-7 | Inline suppression `# assumptionradar: ignore[AR-CWD-001] -- เหตุผล` · strict mode ห้าม ignore แบบไม่ระบุ rule · ตรวจหา suppression ที่ค้าง (ไม่มี finding ให้ ignore แล้ว) | Opus → Haiku | เทส parse + strict + stale | P2-2 |
| P2-8 | Config `[tool.assumptionradar]` ใน pyproject: discovery, `--config`, ลำดับความสำคัญ CLI > ไฟล์ > ค่าเริ่มต้น, error message ที่บอกวิธีแก้ | Opus schema → Haiku | เทส config ผิดทุกรูปแบบได้ exit 2 พร้อมข้อความชัด | P1-14 |
| P2-9 | Baseline: `baseline create` / `baseline compare` เทียบด้วย fingerprint → แยก new / resolved / unchanged | Haiku | เทส workflow ครบ | P1-3 |
| P2-10 | SARIF 2.1.0 exporter + vendor schema ทางการ + เทส validate | Opus mapping → Haiku | รายงานตัวอย่างผ่าน schema | P2-2 |
| P2-11 | CLI: `scan`, `--format json\|sarif\|text`, `--output`, `-q`/`-v`, `config check`, `doctor` | Haiku | เทส CLI | P2-8 |
| P2-12 | Public API: `Radar(rules=..., mode=...)`, `.scan(path)`, `.verify(...)`, `report.to_json()`, `report.to_sarif()`, `report.summary()` + exception classes + docstring | Opus ออกแบบ → Haiku | เทส API + mypy strict | P2-11 |
| P2-13 | Benchmark (static เท่านั้น ไม่รันโค้ด): repo เล็ก/กลาง/ใหญ่ เช่น `requests`, `flask`, `django` → บันทึกจำนวนไฟล์, LOC, เวลา, peak memory, cache hit | Sonnet | `benchmarks/RESULTS.md` เป็นตัวเลขที่วัดจริง | P2-12 |

---

## Phase 3 — v0.3 "Ecosystem + Release"

| ID | งาน | ชั้น | เกณฑ์รับงาน | รอ |
|---|---|---|---|---|
| P3-1 | Plugin contract: `Detector` base class สาธารณะ · entry point group `assumptionradar.detectors` · detector ที่ crash ต้องไม่ทำให้รายงานพัง (ถูกบันทึกเป็น error ของ detector นั้น) · โหมด strict/non-strict | Opus → Haiku | `examples/custom-detector/` ติดตั้งแล้วถูกค้นเจอจริง | P2-12 |
| P3-2 | Test-order investigation (opt-in): สลับลำดับด้วย seed แบบมีจำนวนรอบจำกัด · บันทึก seed และลำดับ · bisect หาคู่เทสที่กระทบกัน · ห้ามอ้างว่าพิสูจน์ order-independence ได้ | Opus → Haiku | fixture เทสที่พึ่งลำดับถูกจับได้และ reproduce ได้ด้วย seed | P1-11 |
| P3-3 ✅ | GitHub Actions: lint, type, test matrix (ubuntu/windows/macos × 3.11–3.13), pip-audit, build + twine check, docs build · **pin action ด้วย commit SHA** | Haiku เขียน → Sonnet ดีบักเมื่อ CI แดง | CI เขียวทุก job | P1-18 + **ผู้ใช้สร้าง GitHub repo** |
| P3-4 | Release workflow: Trusted Publishing (OIDC) + GitHub environment `pypi` ที่ต้องมีคนอนุมัติ · ไม่มี token ฝังในไฟล์ | Haiku | dry-run ไป TestPyPI ผ่าน (**ผู้ใช้ตั้ง trusted publisher**) | P3-3 |
| P3-5 | mkdocs site ครบ 22 หน้าตาม spec (ส่วนไหนที่ยังไม่ได้ implement ให้เขียนว่า "ยังไม่รองรับ") | Opus เขียน · Haiku ตั้ง mkdocs | `mkdocs build --strict` ผ่าน · ตัวอย่างใน docs รันได้ | P3-1 |
| P3-6 | Real-world validation: static scan บน OSS 3–5 repo (ทำในโฟลเดอร์ทิ้งได้) · verify mode **รันเฉพาะใน container/VM** · นับ true/false positive | Sonnet รัน · **Opus ตัดสิน TP/FP** | `docs/validation.md` เป็นตัวเลขจริง | P2-13 |
| P3-7 | เอกสารเปรียบเทียบกับ pytest-socket, pytest-env, pytest-randomly, tox และ ruff DTZ แบบอิงข้อเท็จจริง ไม่อวดเกินจริง | Opus | – | P3-6 |
| P3-8 | CHANGELOG, นโยบาย SemVer, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, tag `v0.3.0` | Haiku (ร่างจาก Opus) | – | P3-5 |
| P3-9 | **Publish ขึ้น PyPI** | **ผู้ใช้อนุมัติเท่านั้น** | – | P3-4 |

---

## เลื่อนไปหลัง v0.3 (ตั้งใจไม่ทำตอนนี้)

- Container isolation mode (Docker/Podman) สำหรับ verify โค้ดที่ไม่น่าไว้ใจ
- Runtime observation ร่วมกับ pytest-xdist
- Mutation testing
- Detector timezone/locale แบบ static (ruff `DTZ` ครอบคลุมส่วนใหญ่แล้ว ให้แนะนำใน docs แทน)
- Network detector แบบ static (pytest-socket ทำ runtime ได้ดีกว่า)

## Blocker ที่ต้องใช้ผู้ใช้

1. เลือก License และชื่อ author ที่จะใส่ใน metadata
2. สร้าง GitHub repo (จำเป็นสำหรับ CI บน macOS/Linux และ project URL)
3. ตั้ง PyPI Trusted Publisher และ GitHub environment ที่ต้องอนุมัติ
4. อนุมัติการ publish

## ลำดับเริ่มต้นที่แนะนำ

`P0-1, P0-2` (ทำพร้อมกันได้) → `P0-3 spike` → `P0-4 ADR` → `P0-5 skeleton` → `P1-1…P1-4` → `P1-5…P1-7` (verification ซึ่งเป็นหัวใจ) → `P1-8…P1-10` → `P1-11…P1-18`
