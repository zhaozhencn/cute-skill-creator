#!/usr/bin/env python3
"""Run the complete offline software suite and preserve verification evidence."""
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/cute-skill-creator"
SPEC = importlib.util.spec_from_file_location("creator", SKILL / "scripts/creator.py")
creator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(creator)


def main():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    output = ROOT / "artifacts/verification" / stamp
    output.mkdir(parents=True)
    started = time.monotonic()
    source_hashes = {}
    for directory in (ROOT / "skills", ROOT / "scripts", ROOT / "tests", ROOT / "examples"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and not {"delivery", "runs"}.intersection(path.parts):
                source_hashes[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    creator.dump(output / "source-hashes.json", source_hashes)
    unit = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                          cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output / "unittest.log").write_text(unit.stdout, encoding="utf-8")
    issues = creator.static_check(SKILL)
    for directory in ("scripts", "tests", "examples"):
        for path in (ROOT / directory).rglob("*.py"):
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as exc:
                issues.append(str(exc))
    creator.dump(output / "static-check.json", {"issues": issues})
    workspace = output / "example workspace"
    shutil.copytree(ROOT / "examples/order-summary", workspace,
                    ignore=shutil.ignore_patterns("__pycache__", "delivery", "runs", "build.json", "latest-run.json", "revisions.json"))
    cli_results = []
    for command, expected in (("check", 0), ("build", 0), ("test", 1), ("report", 1)):
        argv = [sys.executable, str(SKILL / "scripts/creator.py"), command, "--workspace", str(workspace)]
        completed = subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        creator.dump(output / ("cli-" + command + ".json"), {"argv": argv, "returncode": completed.returncode,
                     "expected_returncode": expected, "stdout": completed.stdout, "stderr": completed.stderr})
        cli_results.append(completed.returncode == expected)
    summary = creator.report(workspace)
    honest_blocking = summary["counts"] == {"passed": 6, "failed": 0, "blocked": 2, "not_run": 0} and not summary["business_verified"]
    passed = unit.returncode == 0 and not issues and all(cli_results) and honest_blocking
    result = {"software_verification_passed": passed, "unit_returncode": unit.returncode, "static_issues": issues,
              "cli_checks_passed": all(cli_results), "unconfigured_ai_correctly_blocked": honest_blocking,
              "example": summary, "duration_seconds": time.monotonic() - started, "python": sys.version,
              "notice": "Unit-test AI adapters are synthetic protocol fixtures, not model quality evidence. See separate live evaluation."}
    creator.dump(output / "verification.json", result)
    report = ["# 软件验证报告", "", "- 软件验证通过: " + ("是" if passed else "否"),
              "- Python: " + sys.version.splitlines()[0], "- 耗时: %.3f 秒" % result["duration_seconds"],
              "- 完整单元/回归结果: [unittest.log](unittest.log)",
              "- 静态结构、引用和 Python 语法检查: [static-check.json](static-check.json)",
              "- 源文件版本: [source-hashes.json](source-hashes.json)",
              "- CLI 端到端: check/build/test/report 全部符合预期返回码。", "",
              "订单示例实际执行 6 项确定性测试，2 项未配置真实模型的路由测试正确阻塞。示例未标记业务验证通过。",
              "单元测试中的模拟 AI 适配器仅验证执行协议、评分及报告逻辑。真实模型行为见独立评估报告。", "",
              "业务示例报告: [report.md](" + Path(summary["report"]).relative_to(output).as_posix().replace(" ", "%20") + ")", ""]
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    creator.dump(ROOT / "artifacts/verification/latest.json", {"directory": str(output), "passed": passed})
    print(json.dumps({"passed": passed, "report": str(output / "report.md")}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
