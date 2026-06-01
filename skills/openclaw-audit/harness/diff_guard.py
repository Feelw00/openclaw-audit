#!/usr/bin/env python3
"""
skills/openclaw-audit/harness/diff_guard.py

R-14 fix-hardening, Layer A — 결정적 diff 스캐너.

목적: 우리 fix diff 를 PR 전에 봇 렌즈로 먼저 훑어, 코드로 잡을 수 있는 재발 결함
클래스를 차단한다. post-sol proof *앞* 에서 돌아 loop-until-dry 가 proof 비용을
곱셈으로 늘리지 않게 한다(이 스캐너는 build 불필요, diff 텍스트 + repo 테스트 파일만 읽음).

근거 데이터 (solutions/SOL-0014~0019 verification_log 집계):
- mock/type drift: SOL-0018 P2 (server-restart-sentinel.test.ts 의 vi.mock 가 신규 import
  markSessionDeliveryPlatformOutcomeUnknown 미제공 → 모듈 로드 실패 / check-test-types 적색).
  + 본 파이프라인 자체 사고(#88008: 테스트가 markTaskTerminalById/getTaskById 사용하나 import 누락).
- partial-failure / fail-after-side-effect: SOL-0018 가 3라운드 연속 놓친 클래스 (fix 가 추가한
  write/persist 가 부분 성공 후 또는 그 자체로 실패하는 경로의 회귀 테스트 부재).

검사 (Layer A 범위):
  CHECK 1 (FAIL): vi.mock import-drift — production diff 가 새로 `import { N } from "M"` 한 N 을,
    repo 의 어떤 test 가 vi.mock("M") 하면서 (spread/importOriginal 없이) factory 에 N 을
    제공하지 않으면 그 N 은 테스트 로드 시 undefined → 런타임/타입 실패. (SOL-0018 클래스)
  CHECK 2 (WARN): failure-path 미커버 — production diff 의 새 `await <sideeffect>(` 에 대해,
    변경된 test 들에 그 ident 를 throw/reject 시키는 주입이 없으면 실패경로 미검증 가능성.
  CHECK 3 (WARN): export-drift — production diff 가 새로 export 한 심볼을, 그 모듈을 mock 하는
    test factory 가 (spread 없이) 빠뜨리면 잠재 drift.

종료코드: FAIL 있으면 2, 아니면 0 (WARN 은 0). --json 으로 기계 판독 출력.
판단(invariant 추론)이 필요한 클래스는 Layer B(적대 에이전트)에서 다룬다 — 여기선 안 함.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

SIDE_EFFECT_RE = re.compile(
    r"\b(write|save|persist|mark|ack|commit|delete|move|update|enqueue|fail|sync|flush|"
    r"rename|upsert|insert|remove|clear|store|put|set[A-Z]\w*)\w*",
    re.IGNORECASE,
)
# import { a, b as c } from "x"  /  import {\n a,\n b,\n} from "x"
IMPORT_BLOCK_RE = re.compile(r"import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+[\"']([^\"']+)[\"']", re.S)
EXPORT_NAMED_RE = re.compile(r"export\s+(?:async\s+)?(?:function|const|class)\s+([A-Za-z_]\w*)")
VI_MOCK_RE = re.compile(r"vi\.mock\(\s*[\"']([^\"']+)[\"']")

# Node builtins are mocked per-test-file for unrelated reasons; a new production
# import of one (e.g. randomUUID from node:crypto) is not a vi.mock drift on our code.
_NODE_BUILTINS = {
    "crypto", "fs", "fs/promises", "path", "os", "util", "stream", "events", "http",
    "https", "net", "url", "child_process", "worker_threads", "zlib", "buffer",
    "assert", "process", "timers", "timers/promises", "node:sqlite",
}


def _module_basename(spec: str) -> str:
    """import specifier 의 basename (확장자 제거). './a/b.js' -> 'b'."""
    base = spec.rsplit("/", 1)[-1]
    for ext in (".js", ".ts", ".mjs", ".cjs"):
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    return base


def _parse_diff(diff_text: str) -> dict[str, list[str]]:
    """파일별 added line(본문, 선두 '+' 제거) 수집. '+++ ' 헤더는 제외."""
    added: dict[str, list[str]] = {}
    cur: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            cur = None
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            cur = None if path == "/dev/null" else path
            continue
        if line.startswith("---") or line.startswith("@@"):
            continue
        if cur and line.startswith("+") and not line.startswith("+++"):
            added.setdefault(cur, []).append(line[1:])
    return added


def _is_test(path: str) -> bool:
    return path.endswith(".test.ts") or path.endswith(".test.mts") or ".test." in path


def _is_production_ts(path: str) -> bool:
    return path.endswith(".ts") and not _is_test(path) and not path.endswith(".d.ts")


def _imported_names_by_module(added_lines: list[str]) -> dict[str, set[str]]:
    """production added 라인에서 새로 import 한 {module-basename: {names}}."""
    blob = "\n".join(added_lines)
    out: dict[str, set[str]] = {}
    for names_raw, spec in IMPORT_BLOCK_RE.findall(blob):
        # node: builtins (and bare builtins) are mocked per-test-file for unrelated
        # reasons; a new production import of one is not a vi.mock drift on our code.
        if spec.startswith("node:") or spec in _NODE_BUILTINS:
            continue
        base = _module_basename(spec)
        for tok in names_raw.split(","):
            tok = tok.strip()
            if not tok:
                continue
            # "b as c" -> imported binding is the source name 'b' (mock must provide 'b')
            name = tok.split(" as ")[0].strip().lstrip("type ").strip()
            if re.fullmatch(r"[A-Za-z_]\w*", name):
                out.setdefault(base, set()).add(name)
    return out


def _exported_names(added_lines: list[str]) -> set[str]:
    blob = "\n".join(added_lines)
    names = set(EXPORT_NAMED_RE.findall(blob))
    # barrel re-export 추가: 단독 식별자 + 콤마 라인 (export { ... } 블록 내부로 추정)
    for ln in added_lines:
        m = re.fullmatch(r"\s*([A-Za-z_]\w*),\s*", ln)
        if m:
            names.add(m.group(1))
    return names


def _new_side_effect_awaits(added_lines: list[str]) -> set[str]:
    out: set[str] = set()
    for ln in added_lines:
        for m in re.finditer(r"await\s+(?:\w+\.)?([A-Za-z_]\w*)\s*\(", ln):
            ident = m.group(1)
            if SIDE_EFFECT_RE.match(ident):
                out.add(ident)
    return out


def _find_test_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for root in ("src", "extensions"):
        base = repo / root
        if base.is_dir():
            files.extend(Path(p) for p in glob.glob(str(base / "**" / "*.test.ts"), recursive=True))
            files.extend(Path(p) for p in glob.glob(str(base / "**" / "*.test.mts"), recursive=True))
    return files


def _vi_mock_blocks(text: str) -> list[tuple[str, str]]:
    """파일에서 (mocked-module-basename, factory-or-call-body-text) 추출.
    factory body 는 vi.mock( 다음부터 균형 괄호 끝까지의 거친 슬라이스."""
    out: list[tuple[str, str]] = []
    for m in VI_MOCK_RE.finditer(text):
        spec = m.group(1)
        start = m.start()
        depth = 0
        end = start
        for i in range(text.find("(", start), len(text)):
            c = text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        out.append((_module_basename(spec), text[start:end]))
    return out


def _factory_provides(body: str, name: str) -> bool:
    """mock factory 가 name 을 제공하는지(또는 spread/importOriginal 로 자동 제공하는지)."""
    if "importOriginal" in body or "...actual" in body or re.search(r"\.\.\.\w+", body):
        return True
    # 키로 등장: `name:` 또는 단독 식별자 토큰
    if re.search(r"\b" + re.escape(name) + r"\b", body):
        return True
    return False


def scan(diff_text: str, repo: Path) -> dict[str, Any]:
    added = _parse_diff(diff_text)
    prod = {p: ls for p, ls in added.items() if _is_production_ts(p)}
    changed_tests = {p: ls for p, ls in added.items() if _is_test(p)}

    # production 이 새로 import 한 {module: names}, 새 export, 새 side-effect await
    new_imports: dict[str, set[str]] = {}
    new_exports: set[str] = set()
    new_se_awaits: set[str] = set()
    for ls in prod.values():
        for mod, names in _imported_names_by_module(ls).items():
            new_imports.setdefault(mod, set()).update(names)
        new_exports |= _exported_names(ls)
        new_se_awaits |= _new_side_effect_awaits(ls)

    findings: list[dict[str, Any]] = []

    # 검사할 모듈 집합: 새 import 대상 + (새 export 가 있으면) production 파일 자신의 basename
    target_modules = set(new_imports.keys())
    for p in prod:
        if _exported_names(prod[p]):
            target_modules.add(_module_basename(p))

    test_files = _find_test_files(repo) if (new_imports or new_exports) else []
    for tf in test_files:
        try:
            text = tf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mod, body in _vi_mock_blocks(text):
            if mod not in target_modules:
                continue
            # CHECK 1 (FAIL): 새로 import 된 이름이 mock 에 없음
            for name in sorted(new_imports.get(mod, set())):
                if not _factory_provides(body, name):
                    findings.append(
                        {
                            "check": "vi-mock-import-drift",
                            "severity": "FAIL",
                            "module": mod,
                            "symbol": name,
                            "test_file": str(tf.relative_to(repo)) if str(tf).startswith(str(repo)) else str(tf),
                            "detail": f"vi.mock(\"{mod}\") 가 production 이 새로 import 한 '{name}' 을 "
                            f"제공하지 않음 → 테스트 로드 시 undefined (SOL-0018 클래스).",
                        }
                    )
            # CHECK 3 (WARN): 새 export 가 mock 에 없음
            for name in sorted(new_exports):
                if name not in new_imports.get(mod, set()) and not _factory_provides(body, name):
                    findings.append(
                        {
                            "check": "vi-mock-export-drift",
                            "severity": "WARN",
                            "module": mod,
                            "symbol": name,
                            "test_file": str(tf.relative_to(repo)) if str(tf).startswith(str(repo)) else str(tf),
                            "detail": f"vi.mock(\"{mod}\") 가 새 export '{name}' 미제공 (production 이 이 "
                            f"테스트 경로에서 import 하면 drift).",
                        }
                    )

    # CHECK 2 (WARN): 새 side-effect await 의 실패경로 미커버
    throw_idents: set[str] = set()
    test_blob = "\n".join(ln for ls in changed_tests.values() for ln in ls)
    for m in re.finditer(
        r"(?:mockRejectedValue|mockRejectedValueOnce|toThrow|rejects\.|throw\s+new|reject\()", test_blob
    ):
        pass  # presence handled per-ident below
    for ident in sorted(new_se_awaits):
        # 변경된 테스트에 ident + (throw|reject|mockReject|toThrow) 가 함께 등장하면 커버로 간주
        covered = bool(
            re.search(re.escape(ident), test_blob)
            and re.search(r"(throw|reject|mockReject|toThrow|SIGKILL|crash)", test_blob, re.IGNORECASE)
        )
        if not covered:
            findings.append(
                {
                    "check": "failure-path-untested",
                    "severity": "WARN",
                    "symbol": ident,
                    "detail": f"새 await {ident}(...) 의 실패경로를 강제하는 테스트(throw/reject)가 변경 "
                    f"테스트에 안 보임 (partial-failure 클래스 — Layer B 에서 invariant 확인 권장).",
                }
            )

    fails = [f for f in findings if f["severity"] == "FAIL"]
    return {
        "ok": len(fails) == 0,
        "fail_count": len(fails),
        "warn_count": len(findings) - len(fails),
        "findings": findings,
        "summary": {
            "production_files": sorted(prod.keys()),
            "changed_test_files": sorted(changed_tests.keys()),
            "new_imports": {m: sorted(n) for m, n in new_imports.items()},
            "new_exports": sorted(new_exports),
            "new_side_effect_awaits": sorted(new_se_awaits),
        },
    }


def _git_diff(repo: Path, base_ref: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), "diff", base_ref],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"ERROR: git diff 실패: {out.stderr[:400]}")
    return out.stdout


# ----------------- self-test -----------------
_CLEAN_DIFF = """\
diff --git a/src/infra/m.ts b/src/infra/m.ts
--- a/src/infra/m.ts
+++ b/src/infra/m.ts
@@
+export function markFoo(id: string) {}
diff --git a/src/gateway/consumer.ts b/src/gateway/consumer.ts
--- a/src/gateway/consumer.ts
+++ b/src/gateway/consumer.ts
@@
+import { markFoo } from "../infra/m.js";
+  await markFoo(entry.id);
diff --git a/src/gateway/consumer.test.ts b/src/gateway/consumer.test.ts
--- a/src/gateway/consumer.test.ts
+++ b/src/gateway/consumer.test.ts
@@
+    await expect(run()).rejects.toThrow();
"""

_DRIFT_DIFF = """\
diff --git a/src/infra/m.ts b/src/infra/m.ts
--- a/src/infra/m.ts
+++ b/src/infra/m.ts
@@
+export function markFoo(id: string) {}
diff --git a/src/gateway/consumer.ts b/src/gateway/consumer.ts
--- a/src/gateway/consumer.ts
+++ b/src/gateway/consumer.ts
@@
+import { markFoo } from "../infra/m.js";
+  await markFoo(entry.id);
"""


def _self_test() -> int:
    import tempfile

    failures = []
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        (repo / "src" / "gateway").mkdir(parents=True)
        # CLEAN: 테스트가 markFoo 를 mock factory 에 제공 + 실패경로 테스트 존재
        (repo / "src" / "gateway" / "consumer.test.ts").write_text(
            'vi.mock("../infra/m.js", () => ({ markFoo: vi.fn() }));\n'
            "it('x', async () => { await expect(run()).rejects.toThrow(); });\n",
            encoding="utf-8",
        )
        r_clean = scan(_CLEAN_DIFF, repo)
        if not r_clean["ok"]:
            failures.append(f"CLEAN 케이스가 FAIL 남: {r_clean['findings']}")

        # DRIFT: 같은 테스트지만 mock 가 markFoo 미제공
        (repo / "src" / "gateway" / "consumer.test.ts").write_text(
            'vi.mock("../infra/m.js", () => ({ ackFoo: vi.fn() }));\n',
            encoding="utf-8",
        )
        r_drift = scan(_DRIFT_DIFF, repo)
        fail_checks = [f for f in r_drift["findings"] if f["severity"] == "FAIL"]
        if r_drift["ok"] or not any(f["check"] == "vi-mock-import-drift" for f in fail_checks):
            failures.append(f"DRIFT 케이스를 못 잡음: {r_drift['findings']}")
        # DRIFT 는 failure-path WARN 도 떠야 함 (await markFoo 실패경로 테스트 없음)
        if not any(f["check"] == "failure-path-untested" for f in r_drift["findings"]):
            failures.append("DRIFT 케이스에서 failure-path WARN 누락")

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("SELF-TEST PASSED: clean=ok, drift=fail(vi-mock-import-drift)+warn(failure-path)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="R-14 Layer A: fix diff 결정적 스캐너")
    ap.add_argument("--repo", help="대상 repo/worktree 경로 (git diff 계산용)")
    ap.add_argument("--base-ref", default="upstream/main", help="diff 기준 ref (기본 upstream/main)")
    ap.add_argument("--diff-file", help="git diff 대신 읽을 unified diff 파일")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    ap.add_argument("--self-test", action="store_true", help="내장 픽스처로 검증")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if args.diff_file:
        diff_text = Path(args.diff_file).read_text(encoding="utf-8")
        repo = Path(args.repo) if args.repo else Path.cwd()
    elif args.repo:
        repo = Path(args.repo)
        diff_text = _git_diff(repo, args.base_ref)
    else:
        ap.error("--repo 또는 --diff-file 중 하나 필요 (또는 --self-test)")
        return 2

    result = scan(diff_text, repo)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"diff_guard: {'OK' if result['ok'] else 'FAIL'} "
              f"(FAIL={result['fail_count']}, WARN={result['warn_count']})")
        for f in result["findings"]:
            loc = f.get("test_file", "")
            sym = f.get("symbol", "")
            print(f"  [{f['severity']}] {f['check']} {sym} {loc}")
            print(f"        {f['detail']}")
        if not result["findings"]:
            print("  (no findings)")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
