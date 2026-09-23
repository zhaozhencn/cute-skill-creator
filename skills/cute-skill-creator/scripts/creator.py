#!/usr/bin/env python3
"""Dependency-free state, packaging and evidence tools for the Creator Skill."""
import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

VERSION = "1.1.0"
STATES = {"confirmed", "pending", "assumed"}
MODES = {"code", "ai", "hybrid"}
LAYERS = {"structure", "trigger", "node", "hybrid", "regression"}
CATEGORIES = {"goal", "io", "flow", "rules", "environment", "acceptance"}


class ContractError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def safe_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ContractError("需要非空相对路径: %r" % relative)
    if ".." in Path(relative).parts:
        raise ContractError("路径不能包含 ..: " + relative)
    if Path(relative).as_posix() != relative:
        raise ContractError("路径必须使用规范形式（无 ./ 或重复斜杠）: " + relative)
    target = (root / relative).resolve()
    if target == root.resolve() or root.resolve() not in target.parents:
        raise ContractError("路径越界: " + relative)
    return target


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hash(root):
    values = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ContractError("交付中不能包含符号链接")
        if path.is_file() and "__pycache__" not in path.parts:
            values[path.relative_to(root).as_posix()] = digest(path)
    return values


def fingerprint(project, workspace):
    sources = {}
    for resource in project.get("resources", []):
        path = safe_path(workspace, resource["source"])
        sources[resource["source"]] = digest(path)
    for relative in project.get("test_resources", []):
        sources[relative] = digest(safe_path(workspace, relative))
    raw = json.dumps({"project": project, "sources": sources, "creator": VERSION},
                     sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def validate(project, workspace):
    try:
        return _validate(project, workspace)
    except (TypeError, KeyError, RecursionError) as exc:
        return ["项目字段类型或嵌套结构非法: " + str(exc)]


def _validate(project, workspace):
    """Return actionable gaps instead of allowing partially specified generation."""
    errors = []
    if not isinstance(project, dict):
        return ["project 必须是对象"]
    def need(obj, fields, label):
        for field in fields:
            if not isinstance(obj.get(field), str) or not obj[field].strip():
                errors.append(label + ": 缺少 " + field)
    def rows(key):
        value = project.get(key)
        if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
            errors.append(key + " 必须是对象数组")
            return []
        return value
    def strings(value):
        return isinstance(value, list) and all(isinstance(x, str) and x for x in value)
    def ids(items, label):
        result = set()
        for item in items:
            key = item.get("id")
            if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
                errors.append(label + ": 非法 id")
            elif key in result:
                errors.append(label + ": 重复 id " + key)
            else:
                result.add(key)
        return result
    if project.get("schema_version") != 1:
        errors.append("schema_version 必须为 1")
    name = project.get("name", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 63:
        errors.append("name 必须是少于 64 字符的小写 kebab-case")
    need(project, ["description", "version", "instructions"], "project")
    description = project.get("description", "")
    if isinstance(description, str) and (len(description) > 1024 or "<" in description or ">" in description):
        errors.append("description 长度上限 1024 且不能包含尖括号")
    requirements, nodes, resources, tests = [rows(k) for k in ("requirements", "nodes", "resources", "tests")]
    if not strings(project.get("test_resources", [])):
        errors.append("test_resources 必须是相对路径字符串数组")
    else:
        for relative in project.get("test_resources", []):
            try:
                if not safe_path(workspace, relative).is_file():
                    errors.append("测试资源不存在: " + relative)
            except ContractError as exc:
                errors.append(str(exc))
    requirement_ids, node_ids, test_ids = ids(requirements, "requirement"), ids(nodes, "node"), ids(tests, "test")
    categories = set()
    for req in requirements:
        need(req, ["text", "source", "acceptance"], "requirement")
        state = req.get("status")
        if state not in STATES:
            errors.append("requirement: status 非法")
        if not isinstance(req.get("critical"), bool):
            errors.append("requirement: critical 必须是布尔值")
        if req.get("critical", True) and state != "confirmed":
            errors.append("关键信息待确认: " + str(req.get("id")))
        category = req.get("category")
        if category not in CATEGORIES:
            errors.append("requirement: category 非法")
        else:
            categories.add(category)
    for category in sorted(CATEGORIES - categories):
        errors.append("缺少业务澄清维度: " + category)
    if not nodes:
        errors.append("至少需要一个流程节点")
    destinations = set()
    for resource in resources:
        try:
            source = safe_path(workspace, resource.get("source"))
            target = resource.get("target")
            safe_path(workspace, target)
            if Path(target).parts[0] not in {"scripts", "references", "assets"} or len(Path(target).parts) < 2:
                raise ContractError("resource target 必须位于 scripts/references/assets")
            if target in destinations:
                raise ContractError("重复 resource target: " + target)
            destinations.add(target)
            if not source.is_file():
                raise ContractError("资源不存在: " + str(source))
        except ContractError as exc:
            errors.append(str(exc))
    edges = {}
    all_branches = set()
    for node in nodes:
        label = "node " + str(node.get("id"))
        need(node, ["input", "output", "reason", "constraints", "validation", "failure", "human_intervention"], label)
        if node.get("mode") not in MODES:
            errors.append(label + ": mode 非法")
        for key, choices in (("depends_on", node_ids), ("requirements", requirement_ids)):
            value = node.get(key)
            if not strings(value) or not set(value) <= choices:
                errors.append(label + ": " + key + " 含未知引用或格式错误")
        if not node.get("requirements"):
            errors.append(label + ": 必须关联需求")
        branches = node.get("branches")
        if not strings(branches) or not branches or len(branches) != len(set(branches)):
            errors.append(label + ": branches 必须非空且唯一")
        else:
            all_branches.update(str(node.get("id")) + ":" + branch for branch in branches)
        if node.get("mode") in {"code", "hybrid"}:
            scripts = node.get("scripts")
            if not strings(scripts) or not scripts or not set(scripts) <= destinations or any(not s.startswith("scripts/") for s in scripts):
                errors.append(label + ": 必须关联实际 scripts 资源")
        if node.get("mode") == "hybrid":
            need(node, ["handoff", "validator"], label)
            if node.get("validator") not in destinations:
                errors.append(label + ": validator 未关联资源")
        if isinstance(node.get("id"), str):
            edges[node["id"]] = node.get("depends_on") if strings(node.get("depends_on")) else []
    visiting, visited = set(), set()
    def visit(key):
        if key in visiting:
            errors.append("流程依赖存在环: " + key)
            return
        if key in visited:
            return
        visiting.add(key)
        for dependency in edges.get(key, []):
            visit(dependency)
        visiting.remove(key)
        visited.add(key)
    for key in edges:
        visit(key)
    covered_nodes, covered_requirements, covered_branches, layers, polarities = set(), set(), set(), set(), set()
    semantic_nodes = set()
    for test in tests:
        label = "test " + str(test.get("id"))
        need(test, ["purpose"], label)
        if not isinstance(test.get("required"), bool):
            errors.append(label + ": required 必须是布尔值")
        if test.get("layer") == "e2e":
            errors.append(label + ": 已移除 e2e 测试层，请删除该用例；必要的节点和分支断言应由节点或回归用例覆盖")
        elif test.get("layer") not in LAYERS or test.get("mode") not in MODES:
            errors.append(label + ": layer/mode 非法")
        for key, choices, covered in (("nodes", node_ids, covered_nodes), ("requirements", requirement_ids, covered_requirements),
                                       ("branches", all_branches, covered_branches)):
            value = test.get(key)
            if not strings(value) or not set(value) <= choices:
                errors.append(label + ": " + key + " 含未知引用或格式错误")
            elif test.get("required"):
                covered.update(value)
        if test.get("required"):
            layers.add(test.get("layer"))
            if test.get("mode") in {"ai", "hybrid"} and strings(test.get("nodes")):
                semantic_nodes.update(test["nodes"])
        command = test.get("command")
        if command is not None and (not strings(command) or not command):
            errors.append(label + ": command 必须是非空 argv 数组或 null")
        timeout = test.get("timeout", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            errors.append(label + ": timeout 必须在 (0, 3600] 秒")
        repeats = test.get("repeats", 1)
        if isinstance(repeats, bool) or not isinstance(repeats, int) or not 1 <= repeats <= 20:
            errors.append(label + ": repeats 必须是 1..20 整数")
        if test.get("mode") == "code" and "expected" not in test:
            errors.append(label + ": code 测试必须提供 expected（JSON 精确断言）")
        if test.get("mode") in {"ai", "hybrid"}:
            rubric = test.get("rubric")
            if not isinstance(rubric, dict) or not rubric or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 1 for v in rubric.values()):
                errors.append(label + ": rubric 必须是非空评分阈值对象，范围 0..1")
            if not isinstance(repeats, int) or repeats < 2:
                errors.append(label + ": AI 测试至少重复两次")
            baseline = test.get("baseline")
            if not isinstance(baseline, dict) or baseline.get("kind") not in {"no_skill", "previous_skill"}:
                errors.append(label + ": AI 测试必须声明无 Skill 或旧版对照")
            elif baseline.get("command") is not None and (not strings(baseline["command"]) or not baseline["command"]):
                errors.append(label + ": baseline command 非法")
        if test.get("layer") == "trigger":
            if test.get("mode") != "ai" or not isinstance(test.get("expected"), bool):
                errors.append(label + ": trigger 必须使用真实 AI 路由适配器和布尔 expected")
            elif test.get("required"):
                polarities.add(test["expected"])
    mandatory = {"structure", "trigger", "node", "regression"}
    if any(n.get("mode") == "hybrid" for n in nodes):
        mandatory.add("hybrid")
    for layer in sorted(mandatory - layers):
        errors.append("缺少必测层次: " + layer)
    if polarities != {True, False}:
        errors.append("需要正向和反向必测触发案例")
    for label, missing in (("节点", node_ids - covered_nodes), ("需求", requirement_ids - covered_requirements),
                           ("分支", all_branches - covered_branches)):
        if missing:
            errors.append("必测用例未覆盖" + label + ": " + ", ".join(sorted(missing)))
    for node in nodes:
        if node.get("mode") in {"ai", "hybrid"} and node.get("id") not in semantic_nodes:
            errors.append("AI 节点缺少语义评分测试: " + str(node.get("id")))
    return errors


def load_project(workspace):
    project = read(workspace / "project.json")
    errors = validate(project, workspace)
    if errors:
        raise ContractError("\n".join(errors))
    return project


def static_check(skill):
    issues = []
    path = skill / "SKILL.md"
    if not path.is_file():
        return ["缺少 SKILL.md"]
    content = path.read_text(encoding="utf-8")
    match = re.match(r'^---\nname: ([a-z0-9-]+)\ndescription: (.+)\n---\n', content)
    if not match:
        issues.append("无效 frontmatter（工具生成格式为 name + 单行 description）")
    for markdown in skill.rglob("*.md"):
        for ref in re.findall(r'\]\(([^)]+)\)', markdown.read_text(encoding="utf-8")):
            if re.match(r"[a-zA-Z]+://", ref) or ref.startswith("#"):
                continue
            target = (markdown.parent / ref.split("#")[0]).resolve()
            if skill.resolve() not in target.parents or not target.is_file():
                issues.append("引用不存在或越界: " + str(markdown.relative_to(skill)) + ": " + ref)
    for script in skill.rglob("*.py"):
        try:
            ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        except (SyntaxError, UnicodeError) as exc:
            issues.append("Python 语法错误: " + str(exc))
    return issues


def build(workspace):
    workspace = workspace.resolve()
    project = load_project(workspace)
    identity = fingerprint(project, workspace)
    root = safe_path(workspace, "delivery/" + identity)
    skill = root / project["name"]
    if root.exists():
        manifest = read(root / "manifest.json")
        if manifest["files"] != tree_hash(skill) or static_check(skill):
            raise ContractError("已有交付被修改，请修订源文件重新生成")
    else:
        skill.mkdir(parents=True)
        try:
            for resource in project["resources"]:
                target = safe_path(skill, resource["target"])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(safe_path(workspace, resource["source"]), target)
            content = "---\nname: %s\ndescription: %s\n---\n\n%s\n" % (
                project["name"], json.dumps(project["description"], ensure_ascii=False), project["instructions"].strip())
            (skill / "SKILL.md").write_text(content, encoding="utf-8")
            issues = static_check(skill)
            if issues:
                raise ContractError("\n".join(issues))
            req_lines = ["# 业务需求说明", "", "版本: " + project["version"], ""]
            for req in project["requirements"]:
                req_lines.extend(["## " + req["id"], req["text"], "状态: " + req["status"],
                                  "来源: " + req["source"], "验收: " + req["acceptance"], ""])
            (root / "requirements.md").write_text("\n".join(req_lines), encoding="utf-8")
            flow = ["# 流程设计", ""]
            for node in project["nodes"]:
                flow.extend(["## " + node["id"], ""] + ["- **%s**: %s" % (k, json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v) for k, v in node.items() if k != "id"] + [""])
            (root / "flow.md").write_text("\n".join(flow), encoding="utf-8")
            dump(root / "project.json", project)
            dump(root / "manifest.json", {"fingerprint": identity, "version": project["version"], "files": tree_hash(skill)})
        except Exception:
            shutil.rmtree(root)
            raise
    pointer = {"fingerprint": identity, "skill": skill.relative_to(workspace).as_posix(), "built_at": now()}
    dump(workspace / "build.json", pointer)
    return pointer


def current_build(workspace, project):
    pointer = read(workspace / "build.json")
    if pointer["fingerprint"] != fingerprint(project, workspace):
        raise ContractError("源文件或需求已改变，请重新 build 并重测")
    skill = safe_path(workspace, pointer["skill"])
    manifest = read(skill.parent / "manifest.json")
    if manifest["files"] != tree_hash(skill):
        raise ContractError("交付文件与清单不一致，请恢复或修订后重新 build")
    return pointer, skill


def render_args(command, workspace, skill):
    return [arg.replace("{workspace}", str(workspace)).replace("{skill}", str(skill)).replace("{python}", sys.executable) for arg in command]


def execute(test, command, workspace, skill, directory):
    directory.mkdir(parents=True)
    dump(directory / "input.json", test.get("input"))
    dump(directory / "expected.json", {k: test[k] for k in ("expected", "rubric") if k in test})
    if not command:
        return {"status": "blocked", "reason": test.get("blocked_reason", "执行器/模型未配置"), "duration_seconds": 0}
    argv = render_args(command, workspace, skill)
    dump(directory / "command.json", argv)
    started = time.monotonic()
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    files = {}
    for index, arg in enumerate(argv):
        path = Path(arg)
        if path.is_absolute() and path.is_file():
            files[arg] = digest(path)
            if index > 0 and path.suffix in {".py", ".sh", ".js", ".json"}:
                shutil.copyfile(path, directory / ("source-" + str(index) + path.suffix))
    dump(directory / "execution-files.json", files)
    try:
        process = subprocess.Popen(argv, text=True, encoding="utf-8", errors="replace", stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=directory, env=env,
                                   start_new_session=(os.name == "posix"))
        try:
            stdout, stderr = process.communicate(json.dumps(test.get("input"), ensure_ascii=False), timeout=test.get("timeout", 60))
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(argv, test.get("timeout", 60), output=stdout, stderr=stderr)
        completed = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        for name, value in (("stdout.txt", exc.stdout), ("stderr.txt", exc.stderr)):
            (directory / name).write_bytes(value.encode() if isinstance(value, str) else value or b"")
        return {"status": "failed", "reason": "执行超时", "duration_seconds": time.monotonic() - started}
    except OSError as exc:
        return {"status": "blocked", "reason": str(exc), "duration_seconds": time.monotonic() - started}
    (directory / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (directory / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    result = {"status": "passed", "reason": "断言满足", "duration_seconds": time.monotonic() - started,
              "returncode": completed.returncode, "model": None, "tokens": None}
    if completed.returncode != 0:
        result.update(status="failed", reason="执行器非零退出")
        return result
    try:
        actual = json.loads(completed.stdout)
        result["actual"] = actual
        if test["mode"] == "code":
            if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(test["expected"], sort_keys=True, allow_nan=False):
                raise ContractError("实际结果与 expected 不一致")
        else:
            if not isinstance(actual, dict):
                raise ContractError("AI 执行器必须输出对象")
            for field in ("model", "reviewer", "response", "evidence"):
                if not isinstance(actual.get(field), str) or not actual[field].strip():
                    raise ContractError("AI 结果缺少 " + field)
            result["model"] = actual["model"]
            tokens = actual.get("tokens")
            if tokens is not None and (isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0):
                raise ContractError("tokens 必须是非负整数或 null")
            result["tokens"] = tokens
            scores = actual.get("scores")
            if not isinstance(scores, dict):
                raise ContractError("AI 结果缺少 scores")
            for criterion, threshold in test["rubric"].items():
                score = scores.get(criterion)
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
                    raise ContractError("评分缺失或非法: " + criterion)
                if score < threshold:
                    result.update(status="failed", reason="评分未达标: " + criterion)
            if "expected" in test:
                if "result" not in actual:
                    raise ContractError("AI 输出缺少 result")
                if isinstance(test["expected"], bool) and not isinstance(actual["result"], bool):
                    raise ContractError("AI result 必须是布尔值")
            result["protocol_valid"] = True
            result["score"] = sum(scores[k] for k in test["rubric"]) / len(test["rubric"])
            if "expected" in test and json.dumps(actual.get("result"), sort_keys=True, allow_nan=False) != json.dumps(test["expected"], sort_keys=True, allow_nan=False):
                result.update(status="failed", reason="AI result 与 expected 不一致")
    except ValueError as exc:
        result["protocol_valid"] = False
        result.update(status="failed", reason=str(exc))
    return result


def rollup(attempts):
    states = {a["status"] for a in attempts}
    for state in ("failed", "blocked", "not_run"):
        if state in states:
            return state
    return "passed" if attempts else "not_run"


def run_tests(workspace, selected=None):
    workspace = workspace.resolve()
    project = load_project(workspace)
    pointer, skill = current_build(workspace, project)
    if selected and not set(selected) <= {t["id"] for t in project["tests"]}:
        raise ContractError("未知 case id")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = safe_path(workspace, "runs/" + run_id)
    run_dir.mkdir(parents=True)
    dump(run_dir / "project.json", project)
    for index, relative in enumerate(project.get("test_resources", [])):
        source = safe_path(workspace, relative)
        target = run_dir / "test-resources" / (str(index) + "-" + source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    record = {"run_id": run_id, "started_at": now(), "creator_version": VERSION, "creator_sha256": digest(Path(__file__)), "skill_version": project["version"],
              "fingerprint": pointer["fingerprint"], "environment": {"python": sys.version, "platform": sys.platform},
              "static_issues": static_check(skill), "cases": [], "revisions": read(workspace / "revisions.json") if (workspace / "revisions.json").exists() else []}
    for test in project["tests"]:
        case = {"id": test["id"], "required": test["required"], "layer": test["layer"], "nodes": test["nodes"],
                "requirements": test["requirements"], "branches": test["branches"], "attempts": [], "baseline": []}
        record["cases"].append(case)
        if selected and test["id"] not in selected:
            case.update(status="not_run", reason="本轮未选择")
            continue
        for index in range(test.get("repeats", 1)):
            for kind, command in [("attempts", test.get("command"))] + ([("baseline", test["baseline"].get("command"))] if test["mode"] != "code" else []):
                directory = run_dir / test["id"] / (kind + "-" + str(index + 1))
                attempt = execute(test, command, workspace, skill, directory)
                attempt["evidence_dir"] = directory.relative_to(run_dir).as_posix()
                case[kind].append(attempt)
        # Baseline may fail the quality threshold; only missing/invalid execution blocks comparison.
        case["status"] = rollup(case["attempts"])
        if case["baseline"]:
            comparable = all(a.get("protocol_valid") is True for a in case["baseline"] + case["attempts"])
            if comparable:
                mean = lambda values: sum(a["score"] for a in values) / len(values)
                case["comparison"] = {"baseline_kind": test["baseline"]["kind"], "candidate_mean": mean(case["attempts"]),
                                      "baseline_mean": mean(case["baseline"]), "delta": mean(case["attempts"]) - mean(case["baseline"])}
            elif case["status"] == "passed":
                case.update(status="blocked", reason="对照执行缺失或结果无法评分")
    try:
        current_build(workspace, project)
    except ContractError as exc:
        record["static_issues"].append(str(exc))
    record["finished_at"] = now()
    record["evidence_hashes"] = tree_hash(run_dir)
    dump(run_dir / "results.json", record)
    dump(workspace / "latest-run.json", {"run_id": run_id, "results_sha256": digest(run_dir / "results.json")})
    return report(workspace)


def report(workspace):
    if not (workspace / "latest-run.json").is_file():
        raise ContractError("尚无测试运行记录；先完成 check、build，再运行 test。未执行测试不能生成通过报告。")
    latest = read(workspace / "latest-run.json")
    run_id = latest["run_id"]
    run_dir = safe_path(workspace / "runs", run_id)
    record = read(run_dir / "results.json")
    integrity = []
    if latest.get("results_sha256") != digest(run_dir / "results.json"):
        integrity.append("测试结果记录已改变或缺少摘要；须重新执行测试")
    try:
        project = load_project(workspace)
        pointer, _ = current_build(workspace, project)
        if pointer["fingerprint"] != record["fingerprint"]:
            integrity.append("报告对应旧交付，当前版本必须重测")
        for relative, expected in record["evidence_hashes"].items():
            path = safe_path(run_dir, relative)
            if not path.is_file() or digest(path) != expected:
                integrity.append("证据已改变: " + relative)
    except (ContractError, OSError, ValueError) as exc:
        integrity.append(str(exc))
    required = [c for c in record["cases"] if c["required"]]
    verified = bool(required) and all(c["status"] == "passed" for c in required) and not record["static_issues"] and not integrity
    attempts = [a for c in record["cases"] for a in c["attempts"] + c["baseline"]]
    executed = [c for c in record["cases"] if c["status"] in {"passed", "failed"}]
    triggers = [c for c in executed if c["layer"] == "trigger"]
    rate = lambda cases: sum(c["status"] == "passed" for c in cases) / len(cases) if cases else None
    metrics = {"trigger_accuracy": rate(triggers), "executed_case_rate": len(executed) / len(record["cases"]) if record["cases"] else 0,
               "duration_seconds": sum(a["duration_seconds"] for a in attempts),
               "known_tokens": sum(a.get("tokens") or 0 for a in attempts),
               "token_measurement_complete": bool(attempts) and all(a.get("tokens") is not None for a in attempts),
               "models": sorted({a["model"] for a in attempts if a.get("model")})}
    summary = {"run_id": run_id, "cases_generated": bool(record["cases"]), "static_passed": not record["static_issues"] and not integrity,
               "business_verified": verified, "metrics": metrics, "integrity_issues": integrity,
               "counts": {s: sum(c["status"] == s for c in record["cases"]) for s in ("passed", "failed", "blocked", "not_run")}}
    dump(run_dir / "summary.json", summary)
    labels = {"passed": "通过", "failed": "失败", "blocked": "阻塞", "not_run": "未执行"}
    lines = ["# 测试报告", "", "- Skill 版本: " + record["skill_version"], "- 内容摘要: " + record["fingerprint"],
             "- Creator: " + record["creator_version"] + " / " + record["creator_sha256"],
             "- 运行: " + run_id, "- 环境: " + json.dumps(record["environment"], ensure_ascii=False),
             "- 模型: " + (", ".join(metrics["models"]) or "未记录/无模型执行"),
             "- 已生成测试用例: 是", "- 静态检查通过: " + ("是" if summary["static_passed"] else "否"),
             "- 业务效果验证通过: " + ("是" if verified else "否"), "", "## 质量指标", "", "```json", json.dumps(metrics, ensure_ascii=False, indent=2), "```",
             "", "成功率仅以已执行的对应案例为分母；执行覆盖率另列。未知 Token 不计为零成本。对照差值不等同于统计显著改善。", "",
             "## 逐项结果", "", "| 用例 | 层次 | 必测 | 状态 | 覆盖 | 证据 |", "| --- | --- | --- | --- | --- | --- |"]
    for case in record["cases"]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (case["id"], case["layer"], case["required"], labels[case["status"]],
                     ", ".join(case["requirements"] + case["nodes"] + case["branches"]),
                     "[执行证据](%s/)" % case["id"] if case["attempts"] else "未执行"))
    lines.extend(["", "完整输入、预期、实际输出与日志见逐项证据；结构化结果见 [results.json](results.json)。", "", "## 未解决项", ""])
    for issue in record["static_issues"] + integrity:
        lines.append("- " + issue + "；影响：不可交付为已验证；下一步：修复并重测。")
    for case in record["cases"]:
        if case["status"] != "passed":
            reasons = case.get("reason") or "; ".join(sorted({a["reason"] for a in case["attempts"] + case["baseline"] if a["status"] != "passed"}))
            lines.append("- %s：%s。影响：%s；下一步：补齐条件或修复后重测。" % (case["id"], reasons, "阻止验证通过" if case["required"] else "可选能力未验证"))
    lines.extend(["", "## 修订与重测", "", "本轮用例状态即以上修订的重测结果；旧 runs/ 记录保留。", "", "```json", json.dumps(record["revisions"], ensure_ascii=False, indent=2), "```", ""])
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    summary["report"] = str(run_dir / "report.md")
    return summary


def init(workspace, name):
    workspace.mkdir(parents=True, exist_ok=True)
    if (workspace / "project.json").exists():
        raise ContractError("project.json 已存在，拒绝覆盖")
    project = {"schema_version": 1, "name": name, "version": "0.1.0", "description": "", "instructions": "",
               "requirements": [{"id": category, "category": category, "text": "", "status": "pending", "critical": True,
                                 "source": "", "acceptance": ""} for category in sorted(CATEGORIES)],
               "nodes": [], "resources": [], "tests": []}
    dump(workspace / "project.json", project)
    return {"project": str(workspace / "project.json"), "phase": "clarification"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "check", "build", "test", "report", "revise"])
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--name", default="new-business-skill")
    parser.add_argument("--case", action="append")
    parser.add_argument("--issue")
    parser.add_argument("--change")
    args = parser.parse_args(argv)
    workspace = args.workspace.resolve()
    try:
        if args.command == "init":
            value = init(workspace, args.name)
        elif args.command == "check":
            errors = validate(read(workspace / "project.json"), workspace)
            value = {"ready": not errors, "gaps": errors}
        elif args.command == "build":
            value = build(workspace)
        elif args.command == "test":
            value = run_tests(workspace, args.case)
        elif args.command == "report":
            value = report(workspace)
        else:
            if not args.issue or not args.change:
                raise ContractError("revise 需要 --issue 和 --change")
            revisions_path = workspace / "revisions.json"
            history = read(revisions_path) if revisions_path.exists() else []
            history.append({"at": now(), "issue": args.issue, "change": args.change,
                            "previous_run": read(workspace / "latest-run.json") if (workspace / "latest-run.json").exists() else None})
            dump(revisions_path, history)
            value = history[-1]
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 1 if value.get("ready") is False or value.get("business_verified") is False else 0
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
