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

**สถานะ P1-12 (2026-11-07):** ✅ เสร็จ · 364 tests ผ่าน · ruff และ mypy strict ผ่าน
- schema (JSON Schema draft 2020-12) อยู่ที่ `src/assumptionradar/reporting/schemas/report-v1.json` และ**ติดไปกับ wheel** (ไม่ได้วางไว้ที่ `docs/schema/` ตามแผนเดิม เพื่อให้มีที่เดียวและให้ `report show` ใน P1-14 ใช้ตรวจได้) · โหลดผ่าน `load_report_schema()`
- schema ปิดไม่ให้มี key ที่ไม่รู้จัก (`additionalProperties: false`) · ค่า enum ใน schema มีเทสบังคับให้ตรงกับ enum ในโค้ด · path ต้องเป็นแบบ relative POSIX
- เทส: เอกสาร schema ถูกต้องตาม draft 2020-12 · รายงานตัวอย่างที่ครอบทุกกิ่ง (null/ไม่ null) ผ่าน · รายงานที่ถูกแก้ให้ผิด 20 แบบถูกปฏิเสธทุกแบบ · **รายงานทุกฉบับจาก pytest run จริงใน integration tests ถูก validate ด้วย** (รวม observe+verify ในรันเดียว)
- CI ขั้น package ตรวจเพิ่มว่า wheel ที่ติดตั้งแล้วโหลด schema ได้
- เพิ่ม `types-jsonschema` ใน dev extras สำหรับ mypy
- ลำดับ key คงที่มาจากโค้ด (`to_dict`) · JSON Schema ตรวจลำดับ key ไม่ได้

**สถานะ P1-13 (2026-11-07):** ✅ เสร็จ · 400 tests ผ่าน · ruff และ mypy strict ผ่าน
- `reporting/terminal.py` แสดงผลจาก**รายงาน v1** (dict เดียวกับ JSON) เพื่อให้ plugin กับ `report show` (P1-14) ใช้ตัวเดียวกัน · ยังไม่แสดง `Finding` เพราะยังไม่มีขั้นตอนไหนสร้าง Finding (รอ static scanner ใน Phase 2)
- verification ขึ้นก่อน เรียงตามความรุนแรง (regression → error → inconclusive → unsupported → no_difference) · regression ระบุชนิดหลักฐาน `(reproduced)` และมีบรรทัด `fix:` ตามชนิด scenario
- observation ขึ้นเหตุการณ์ที่น่าสงสัยก่อนโดยมีเครื่องหมาย `!` (path แบบ relative · เรียกโปรแกรมผ่าน PATH · อ่าน env var ที่ไม่มี) · ตำแหน่งเป็น `file:line`
- สีรองรับ `NO_COLOR` และ `FORCE_COLOR` (ใช้ใน CLI) · ใน pytest ให้ pytest จัดการสีเอง
- snapshot tests ระบุข้อความที่คาดไว้ทุกตัวอักษร · Haiku เขียน implementation ตามเทสผ่านในรอบเดียว
- แก้บั๊กระหว่างทาง: ข้อความ "verifying…" เคยไปต่อท้ายบรรทัด `[100%]` ของ pytest

**สถานะ P1-14 (2026-11-07):** ✅ เสร็จ · 439 tests ผ่าน · ruff และ mypy strict ผ่าน
- คำสั่ง: `scenarios`, `explain <name>`, `report show <json>` (`--format text|json`, `--color auto|always|never`, `--exit-zero`) · ทุกคำสั่งมี `--format json` ที่ stdout เป็น JSON ล้วน (ASCII-safe)
- **เปลี่ยนจากแผน:** ใช้ `scenarios` แทน `rules` และ `explain` รับชื่อ scenario เพราะกฎ `AR-*` ยังไม่มีจนกว่าจะถึง Phase 2 · Phase 2 จะเพิ่ม `rules` และให้ `explain` รับ rule id
- exit code: 0 ผ่าน · 1 มี regression · 2 ใช้คำสั่งผิดหรือรายงานเสีย/ผิด schema · 3 internal error (ไม่มี traceback ให้ผู้ใช้เห็น) · 4 inconclusive/error
- `core/catalog.py` เป็นที่เดียวของคำอธิบาย scenario และข้อความ `fix:` (terminal renderer ดึงจากที่นี่) · มีเทสบังคับให้ catalog ตรงกับ scenario จริง
- ตรวจรูปร่างรายงานเองโดยไม่ใช้ jsonschema ตอนรัน → ไม่เพิ่ม dependency
- ข้อความที่ terminal เข้ารหัสไม่ได้ (เช่น console cp874) ถูกแทนด้วย `?` แทนที่จะ crash
- CI ขั้น package รัน `assumptionradar scenarios` จาก wheel ที่ติดตั้งแล้ว

**สถานะ P1-15 (2026-11-07):** ✅ เสร็จ · 441 tests ผ่าน และไม่มีเทสถูก skip ทั้ง 12 job ใน CI (ubuntu/windows/macos × 3.11–3.14) · ผลตรงตามที่คาดไว้ทุกตัวบนทั้ง 3 OS
- `tests/fixtures/positive`: 5 เทสที่ผ่านบนเครื่องผู้เขียนแต่ฝังสมมติฐานไว้ ได้แก่ เปิด `config.json` แบบ relative · เวลา local เทียบกับ snapshot ที่บันทึกบนเครื่องผู้เขียน · ต้องมี env var · เรียก `git` ผ่าน PATH · `open()` โดยไม่ระบุ encoding
- `tests/fixtures/negative`: งานเดียวกันแบบ portable (`Path(__file__).parent`, ระบุ tz, `monkeypatch.setenv`, path เต็มของโปรแกรม, `encoding="utf-8"`)
- `tests/e2e/test_fixture_projects.py` รัน plugin จริงใน process แยก แล้วเทียบผลแบบ**ตรงทุกตัว**: verdict ของทุกเทสทุกกลุ่ม scenario และชุด event ที่ถูกติด `!` · positive ต้องได้ regression ตรงตามที่ฝังไว้และไม่มีเกิน · negative ต้องไม่มีอะไรเลย · รายงานผ่าน schema และ `report show` ให้ exit code 1 / 0 ตามลำดับ
- ไฟล์ "ที่บันทึกบนเครื่องผู้เขียน" (เวลา local, ไฟล์ข้อความใน encoding ของเครื่อง) สร้างตอนรันเทส จึงผ่าน baseline ได้ทุกเครื่อง
- fixture ไม่ถูกเก็บโดยชุดเทสหลัก (`collect_ignore`) และมี per-file ignore ของ ruff เพราะตั้งใจเขียนผิด
- ข้อจำกัด: `git` ผ่าน PATH จับได้จาก observation เท่านั้น (ยังไม่มี scenario ที่ตัด PATH) · e2e ใช้เวลา ~45 วินาที

**สถานะ P1-16 (2026-11-07):** ✅ เสร็จ · 477 tests ผ่าน · ruff และ mypy strict ผ่าน
- `tests/security/` ทดสอบผ่าน plugin จริง: ค่าลับไม่หลุดเข้ารายงานและ terminal summary · home directory ไม่อยู่ในรายงาน · state ของ process แม่ไม่เปลี่ยน (env, cwd, tz, locale, tempdir, `os.environ`) เทียบกับ control run · timeout ตัดเทสที่ค้างได้และ process ลูกหายจริง · output ในรายงานไม่เกิน 4,000 ตัวอักษร
- **บั๊กที่เจอและแก้:**
  1. regex ใน `redact_text` 2 ตัว backtrack แบบกำลังสอง → output ยาว 200K ตัวอักษรทำให้ pytest run ค้างไม่จบ · แก้ให้ linear (1MB ≈ 0.1 วินาที) + เทสความเร็ว
  2. ค่าลับที่โผล่แบบไม่มี `key=` นำหน้า (ใน path หรือข้อความ assertion) หลุดเข้ารายงาน · แก้: `secret_env_values()` ดึงค่าของ env var ที่ชื่อดูเป็นความลับ (TOKEN, PASSWORD, SECRET, CREDENTIAL, APIKEY, `*_KEY`; ยาว ≥ 8) ไปปิดทับทั้งใน output ของ child และใน event ของ observer
- ข้อจำกัด: ค่าลับที่ไม่ได้มาจาก env var และไม่ตรง pattern (เช่น อ่านจากไฟล์แล้วพิมพ์ออกมาเฉยๆ) ยังกันไม่ได้ · เขียนไว้ใน docs ตอน P1-17

**สถานะ P1-17 (2026-11-08):** ✅ เสร็จ · 490 tests ผ่าน · ruff และ mypy strict ผ่าน
- เอกสาร: `README.md` (เขียนใหม่), `docs/quickstart.md`, `docs/limitations.md`, `docs/security.md` (ระบุชัดว่า re-run **ไม่ใช่ sandbox**)
- `tests/docs/test_doc_examples.py` รันทุก code block จริง: ทุกบล็อกต้องมี marker (`file` / `run` / `output` / `skip` พร้อมเหตุผล) ไม่งั้นเทสตก · คำสั่งถูกรันใน sandbox แยกต่อเอกสาร · output ที่แสดงในเอกสารต้องตรงกับของจริง (`...` ข้ามส่วนที่ต่างตามเครื่อง) · ลิงก์ relative ทุกอันต้องชี้ไฟล์ที่มีอยู่
- บล็อกที่ skip มี 3 บล็อก: `pip install` จาก GitHub (ต้องใช้เน็ต; CI ขั้น package ทดสอบ wheel แทน), ขั้นตอน development, ตัวอย่าง YAML ของ CI
- ทุกข้อความในเอกสารเช็คกับโค้ดจริงแล้ว (audit events ที่รองรับ, ค่า default, กฎ redaction)
- ยังไม่ได้ทำ: private vulnerability reporting บน GitHub ยังปิดอยู่ · `docs/security.md` จึงให้เปิด issue ขอช่องทางติดต่อแบบไม่ใส่รายละเอียดแทน

**สถานะ P1-18 (2026-11-08):** ✅ เสร็จ · `tools/release_check.py` ผ่านทุกขั้นบน Windows (Python 3.14)
- ขั้นตอน: build sdist + wheel → `twine check --strict` → ตรวจไฟล์ใน wheel (schema, `py.typed`, entry point ของ CLI และ pytest11, ไม่มี tests/tools, ไม่มี runtime dependency, License MIT) → ลิงก์ใน README ต้องเป็น absolute → ติดตั้ง wheel + pytest ใน venv ใหม่แล้วทดสอบ CLI, pytest ปกติไม่ถูกกระทบ, option ขึ้นใน `--help`, plugin จับ regression ได้และ `--assumption-strict` ให้ exit 1, `report show` → ติดตั้ง sdist ใน venv ใหม่ (build จาก source) แล้วเช็คเวอร์ชันและ schema
- เจอและแก้: ลิงก์ใน README เป็น relative ซึ่งจะเสียบนหน้า PyPI → เปลี่ยนเป็น URL เต็มของ GitHub
- CI ขั้น package รันสคริปต์นี้ด้วย จึงไม่ล้าสมัย
- เวอร์ชันยังเป็น `0.1.0.dev0` · การตั้งเวอร์ชันจริงและ publish อยู่ใน Phase 3 (P3-4, P3-9 ต้องให้ผู้ใช้อนุมัติ)

**Phase 1 (v0.1 "Verify") ครบทุกงานแล้ว**

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

**สถานะ P2-1 (2026-11-11):** ✅ เสร็จ · 556 tests ผ่าน (1 skip: เทส symlink บน Windows) · ruff และ mypy strict ผ่าน
- ออกแบบไว้ใน ADR-0005 · แพ็กเกจ `assumptionradar.static`: `discovery` (glob, default excludes, จำกัดขนาด, ไม่ตาม symlink) · `parsing` (`tokenize.detect_encoding` + `ast.parse` ไม่รันโค้ด) · `imports` (ตาราง alias, resolve `builtins.*`) · `cache` (JSON ตาม sha256, เขียนแบบ atomic) · `engine.scan()` (rule ที่ crash กลายเป็น error ของไฟล์นั้น ไม่ทำให้ scan พัง)
- เพิ่ม `Finding/Evidence/SourceLocation.from_dict` เพื่อให้อ่านจาก cache ได้ตรงทุกฟิลด์
- เจอและแก้: ไฟล์ที่มี NUL byte ทำให้ `tokenize` พัง → ตอนนี้รายงานเป็น syntax error
- ยังไม่มี rule จริง (เริ่มที่ P2-2)

**สถานะ P2-2 (2026-11-11):** ✅ เสร็จ · 592 tests ผ่าน · ruff และ mypy strict ผ่าน
- `static/rules/cwd.py` (`AR-CWD-001`): ตรวจ relative path ที่ถูกเข้าถึงจริง ทั้ง `open`/`io`/`codecs`/`os`/`os.path`/`shutil`/`glob`/`sqlite3`/`tarfile`/`zipfile` และ method ของ `Path` (`read_text`, `exists`, `glob`, ...) รวมถึง `Path("a") / "b"`, `os.path.join("a", ...)` และ f-string (confidence medium) · ไม่ตรวจ path ที่สร้างอย่างเดียว · `default_rules()` ใน `static/rules/__init__.py`
- ไม่นับเป็น relative: absolute, drive letter, UNC, `~`, URL, `file:`, `:memory:`, `NUL` · ข้ามการเรียกที่มี `dir_fd`/`root_dir`
- ชุดเทส fixture ใช้ซ้ำได้: `tests/fixtures/static/<กลุ่ม>/*.py` ใส่ comment `# expect: <rule id>` ไว้ท้ายบรรทัด แล้วเทียบผลแบบตรงตัว (negative ต้องไม่มี finding เลย)
- ลองสแกน repo นี้เอง: เจอ 10 จุดใน `tests/unit/test_runtime.py` ซึ่งถูกทั้งหมด (เทสนั้นใช้ `monkeypatch.chdir` ไว้โดยตั้งใจ) · ไม่มีจุดที่เจอผิด
- ข้อจำกัด: ไม่ติดตามตัวแปร (`p = Path("x"); p.read_text()` ไม่ถูกจับ) และไม่รู้ว่าโค้ดเปลี่ยน cwd ไว้ก่อนแล้ว

**สถานะ P2-3 (2026-11-11):** ✅ เสร็จ · 605 tests ผ่าน · ruff และ mypy strict ผ่าน
- `static/rules/env.py` · `AR-ENV-001` (เป็น default rule): จับ `os.environ["X"]` และ `os.environ.pop("X")` ที่ไม่มี default · ไม่จับเมื่อใน scope เดียวกันหรือ scope ที่ครอบอยู่มีการเช็ค (`in`/`not in`, `.get`, `getenv`), มีการตั้งค่า (`setenv`, `os.environ["X"] = ...`, `setdefault`) หรืออยู่ใน `try` ที่จับ `KeyError`/`LookupError`/`Exception`/bare except · key ที่ไม่ใช่ literal ได้ confidence medium
- `AR-ENV-002` (ยังไม่เป็น default): รายการ env var ที่ไฟล์อ่าน 1 finding ต่อชื่อต่อไฟล์ ยกเว้นชื่อที่ประกาศไว้ · version ของ rule มี hash ของรายชื่อที่ประกาศ เพื่อให้ cache เปลี่ยนตาม · จะเปิดใช้เมื่อมี config ใน P2-8 · `collect_env_reads()` ใช้ได้กับ CLI ภายหลัง
- เปลี่ยนชื่อไฟล์ fixture เป็น `positive_<กลุ่ม>.py`/`negative_<กลุ่ม>.py` เพราะ mypy ไม่ยอมให้มีชื่อโมดูลซ้ำกัน
- ผลกับโปรเจค e2e: fixture positive ได้ `AR-CWD-001` + `AR-ENV-001` ตรงกับที่ runtime พบ · fixture negative ไม่มี finding · ใน repo นี้ไม่มีจุดที่เจอผิด

**สถานะ P2-4 (2026-11-11):** ✅ เสร็จ · 645 tests ผ่าน · ruff และ mypy strict ผ่าน (ทั้ง Windows และ `--platform linux`)
- `static/rules/exe.py` (`AR-EXE-001`, เป็น default rule): จับคำสั่งที่เรียกด้วยชื่อเปล่า (พึ่ง PATH) ใน `subprocess.*`, `os.system`/`os.popen`, `os.exec*p`/`os.spawn*p`, `asyncio.create_subprocess_*` · รูปแบบ list/tuple, `[...] + x`, และ string (ถ้าผ่าน shell จะข้าม `FOO=1` และ shell builtin เช่น `cd`/`echo`/`dir`) · **ไม่รันคำสั่ง**
- ไม่จับ: `sys.executable`, absolute path, relative path (`./build.sh`), ชื่อในตัวแปร, `executable=` และเครื่องมือที่ถูกเช็คด้วย `shutil.which("x")` ใน scope เดียวกันหรือ scope ที่ครอบอยู่ (ครอบคลุม `@pytest.mark.skipif(...)` และ `pytestmark`)
- string ที่ผ่าน shell ได้ confidence medium · ถ้าเป็น `python`/`pip` จะแนะนำให้ใช้ `sys.executable`
- ผล: fixture e2e positive ได้ครบ 3 rule (`config.json`, env var, `git`) ตรงกับที่ runtime พบ · ใน repo นี้เจอ 2 จุด ซึ่งถูกทั้งคู่ (`tasklist` ในเทส security, เครื่องมือที่ตั้งใจให้ไม่มีใน test_runtime)

**สถานะ P2-5 (2026-11-11):** ✅ เสร็จ · 674 tests ผ่าน · ruff และ mypy strict ผ่าน (Windows + `--platform linux`)
- `static/rules/plat.py` (`AR-PLAT-001`, เป็น default rule) รายงาน 4 แบบ (`metadata.kind`): `windows-path` (drive letter, UNC, backslash) และ `posix-path` (absolute `/...`, confidence medium) **เฉพาะ path ที่ถูกเข้าถึงจริง** (ส่งเข้าฟังก์ชัน file system หรือเรียก method ของ `Path`) · `posix-api`/`windows-api` (เช่น `os.fork`, `signal.SIGKILL`, `os.startfile`, `ctypes.windll`) และการ import โมดูลเฉพาะ OS (`fcntl`, `pwd`, `termios`, `msvcrt`, `winreg`, ...)
- ไม่จับเมื่อ scope เดียวกันหรือ scope ที่ครอบอยู่อ้างถึง `sys.platform`/`os.name`/`platform.system()`/`hasattr(os, ...)` (รวม `skipif` และ early return), อยู่ใต้ `if TYPE_CHECKING:` หรืออยู่ใน `try` ที่จับ `ImportError`/`AttributeError`/`OSError` · `PureWindowsPath`/`PurePosixPath` ถือว่าตั้งใจ
- ชุดเทส fixture เปลี่ยนให้แต่ละกลุ่มนับเฉพาะ rule ของตัวเอง (`cwd/` นับ `AR-CWD-*` เป็นต้น) · fixture `plat/` ถูกยกเว้นจาก mypy เพราะใช้ API ของหลาย OS โดยตั้งใจ · cwd.py เปิด `FILE_SINKS`/`select_argument` ให้ rule อื่นใช้ร่วม
- เจอและแก้ระหว่างตรวจ: spec แรกนับ `Path("/home/bob")` ที่แค่สร้างไว้เป็นค่า (ใน test_redact) ว่าผิด → เปลี่ยนเป็นนับเฉพาะ path ที่ถูกเข้าถึง ให้สอดคล้องกับ `AR-CWD-001` · ตอนนี้ใน repo นี้ไม่มี finding ของ `AR-PLAT-001`

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
