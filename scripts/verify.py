#!/usr/bin/env python3
"""Run the complete offline software suite and preserve verification evidence."""
import ast
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
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
    passed = unit.returncode == 0 and not issues
    result = {"software_verification_passed": passed, "unit_returncode": unit.returncode, "static_issues": issues,
              "duration_seconds": time.monotonic() - started, "python": sys.version,
              "notice": "Unit-test AI adapters are synthetic protocol fixtures, not model quality evidence. See separate live evaluation."}
    creator.dump(output / "verification.json", result)
    report = ["# 软件验证报告", "", "- 软件验证通过: " + ("是" if passed else "否"),
              "- Python: " + sys.version.splitlines()[0], "- 耗时: %.3f 秒" % result["duration_seconds"],
              "- 完整单元/回归结果: [unittest.log](unittest.log)",
              "- 静态结构、引用和 Python 语法检查: [static-check.json](static-check.json)",
              "- 源文件版本: [source-hashes.json](source-hashes.json)", "",
              "单元测试中的模拟 AI 适配器仅验证执行协议、评分及报告逻辑。真实模型行为见独立评估报告。", ""]
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    creator.dump(ROOT / "artifacts/verification/latest.json", {"directory": str(output), "passed": passed})
    print(json.dumps({"passed": passed, "report": str(output / "report.md")}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
