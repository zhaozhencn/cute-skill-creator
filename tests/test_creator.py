"""Framework tests use synthetic AI adapters; these are NOT model quality evidence."""
import copy
import contextlib
import io
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/cute-skill-creator/scripts/creator.py"
SPEC = importlib.util.spec_from_file_location("creator", SCRIPT)
creator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(creator)
EXAMPLE = ROOT / "examples/order-summary"


class CreatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cute-creator-test-")
        self.workspace = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.project = json.loads((EXAMPLE / "project.json").read_text())
        for filename in ("summarize.py", "check_structure.py"):
            (self.workspace / filename).write_bytes((EXAMPLE / filename).read_bytes())
        self.save()

    def save(self):
        creator.dump(self.workspace / "project.json", self.project)

    def build(self):
        self.save()
        pointer = creator.build(self.workspace)
        return self.workspace / pointer["skill"]

    def run_all(self):
        self.build()
        return creator.run_tests(self.workspace)

    def synthetic_ai(self):
        # Explicit protocol doubles, not real AI behavior or benchmark data.
        path = self.workspace / "synthetic_ai.py"
        path.write_text("import json, sys\nx=json.load(sys.stdin)\nprint(json.dumps(dict(result=x['expected'], response='SYNTHETIC PROTOCOL FIXTURE', model='synthetic-test-double', reviewer='unit-test', evidence='protocol fixture only', scores={'routing':1}, tokens=10)))\n")
        for case in self.project["tests"]:
            if case["mode"] == "ai":
                case["input"]["expected"] = case["expected"]
                case["command"] = ["{python}", "{workspace}/synthetic_ai.py"]
                case["baseline"]["command"] = case["command"]

    def result_record(self):
        run_id = creator.read(self.workspace / "latest-run.json")["run_id"]
        return creator.read(self.workspace / "runs" / run_id / "results.json")

    def errors(self):
        return creator.validate(self.project, self.workspace)

    def test_example_contract_ready(self):
        self.assertEqual([], self.errors())

    def test_removed_e2e_layer_requires_migration(self):
        case = copy.deepcopy(self.project["tests"][1])
        case.update(id="legacy-e2e", layer="e2e")
        self.project["tests"].append(case)
        self.assertTrue(any("已移除 e2e 测试层" in x for x in self.errors()))
        with self.assertRaises(creator.ContractError):
            self.build()

    def test_init_preserves_existing_project(self):
        with self.assertRaises(creator.ContractError):
            creator.init(self.workspace, "new-skill")

    def test_init_explicitly_pending_and_blocked(self):
        target = self.workspace / "new"
        creator.init(target, "new-skill")
        project = creator.read(target / "project.json")
        self.assertTrue(all(r["status"] == "pending" for r in project["requirements"]))
        self.assertTrue(creator.validate(project, target))
        with self.assertRaises(creator.ContractError):
            creator.build(target)

    def test_critical_assumption_blocks_generation(self):
        self.project["requirements"][0]["status"] = "assumed"
        self.assertTrue(any("关键信息" in x for x in self.errors()))
        with self.assertRaises(creator.ContractError):
            self.build()

    def test_noncritical_assumption_preserved(self):
        requirement = self.project["requirements"][0]
        requirement.update(status="assumed", critical=False)
        skill = self.build()
        self.assertIn("assumed", (skill.parent / "requirements.md").read_text())

    def test_missing_clarification_dimensions(self):
        self.project["requirements"] = self.project["requirements"][:1]
        self.assertTrue(any("维度" in x for x in self.errors()))

    def test_missing_node_contract_fields(self):
        for field in ("input", "output", "reason", "constraints", "validation", "failure", "human_intervention"):
            with self.subTest(field=field):
                value = self.project["nodes"][0].pop(field)
                self.assertTrue(any(field in x for x in self.errors()))
                self.project["nodes"][0][field] = value

    def test_duplicate_ids_rejected(self):
        for field in ("requirements", "nodes", "tests"):
            with self.subTest(field=field):
                self.project[field].append(copy.deepcopy(self.project[field][0]))
                self.assertTrue(any("重复 id" in x for x in self.errors()))
                self.project[field].pop()

    def test_invalid_names_and_case_paths(self):
        for name in ("../bad", "UPPER", "a--b", "-bad", "a" * 64):
            self.project["name"] = name
            self.assertTrue(self.errors())
        self.project["tests"][0]["id"] = "../../escape"
        self.assertTrue(any("非法 id" in x for x in self.errors()))

    def test_cycle_rejected(self):
        self.project["nodes"][0]["depends_on"] = ["sum"]
        self.assertTrue(any("存在环" in x for x in self.errors()))

    def test_unknown_dependency_and_branch_rejected(self):
        self.project["nodes"][0]["depends_on"] = ["missing"]
        self.project["tests"][0]["branches"] = ["sum:unknown"]
        self.assertGreaterEqual(len(self.errors()), 2)

    def test_uncovered_branch_rejected(self):
        self.project["nodes"][0]["branches"].append("manual")
        self.assertTrue(any("未覆盖分支" in x for x in self.errors()))

    def test_optional_cases_cannot_satisfy_coverage(self):
        for case in self.project["tests"]:
            case["required"] = False
        self.assertTrue(any("未覆盖需求" in x for x in self.errors()))

    def test_trigger_requires_real_ai_mode_and_both_polarities(self):
        self.project["tests"][-1]["expected"] = True
        self.assertTrue(any("反向" in x for x in self.errors()))
        self.project["tests"][-1]["mode"] = "code"
        self.assertTrue(any("真实 AI" in x for x in self.errors()))

    def test_ai_requires_repeats_rubric_baseline(self):
        case = self.project["tests"][-1]
        case.update(repeats=1, rubric={}, baseline=None)
        self.assertGreaterEqual(len(self.errors()), 3)

    def test_code_requires_actual_script(self):
        self.project["nodes"][0]["scripts"] = []
        self.assertTrue(any("scripts" in x for x in self.errors()))

    def test_hybrid_requires_validator_handoff_and_layer(self):
        self.project["nodes"][0]["mode"] = "hybrid"
        errors = self.errors()
        self.assertTrue(any("handoff" in x for x in errors))
        self.assertTrue(any("validator" in x for x in errors))
        self.assertTrue(any("必测层次: hybrid" in x for x in errors))
        self.assertTrue(any("语义评分" in x for x in errors))

    def test_resource_traversal_and_absolute_path_rejected(self):
        for key, value in (("source", "../outside"), ("target", "/tmp/escape"), ("target", "SKILL.md")):
            with self.subTest(key=key, value=value):
                old = self.project["resources"][0][key]
                self.project["resources"][0][key] = value
                self.assertTrue(self.errors())
                self.project["resources"][0][key] = old

    def test_symlink_source_escape_rejected(self):
        (self.workspace / "escape.py").symlink_to(SCRIPT)
        self.project["resources"][0]["source"] = "escape.py"
        self.assertTrue(any("越界" in x for x in self.errors()))

    def test_duplicate_resource_target_rejected(self):
        self.project["resources"].append(copy.deepcopy(self.project["resources"][0]))
        self.assertTrue(any("重复 resource" in x for x in self.errors()))

    def test_bad_limits_rejected(self):
        for field, values in (("timeout", [0, -1, True, float("inf"), 3601]), ("repeats", [0, True, 1.5, 21])):
            for value in values:
                self.project["tests"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.assertTrue(self.errors())

    def test_build_self_contained_no_test_material(self):
        skill = self.build()
        self.assertTrue((skill / "scripts/summarize.py").is_file())
        self.assertFalse((skill / "tests").exists())
        self.assertFalse((skill / "project.json").exists())
        self.assertEqual([], creator.static_check(skill))
        self.assertTrue((skill.parent / "flow.md").exists())
        self.assertTrue((skill.parent / "requirements.md").exists())

    def test_build_idempotent_and_source_change_keeps_previous_version(self):
        first = self.build()
        self.assertEqual(first, self.build())
        source = self.workspace / "summarize.py"
        source.write_text(source.read_text() + "\n# revision\n")
        second = self.build()
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_dir())

    def test_broken_link_and_syntax_rejected_and_cleanup_allows_retry(self):
        self.project["instructions"] += "\n[missing](references/missing.md)"
        with self.assertRaises(creator.ContractError):
            self.build()
        self.project["instructions"] = "Run the script."
        (self.workspace / "summarize.py").write_text("def broken(:")
        with self.assertRaises(creator.ContractError):
            self.build()
        (self.workspace / "summarize.py").write_bytes((EXAMPLE / "summarize.py").read_bytes())
        self.assertTrue(self.build().exists())

    def test_example_business_executes_and_ai_honestly_blocked(self):
        summary = self.run_all()
        self.assertEqual({"passed": 5, "failed": 0, "blocked": 2, "not_run": 0}, summary["counts"])
        self.assertTrue(summary["static_passed"])
        self.assertFalse(summary["business_verified"])
        self.assertIsNone(summary["metrics"]["trigger_accuracy"])
        self.assertFalse(summary["metrics"]["token_measurement_complete"])
        self.assertNotIn("task_success_rate", summary["metrics"])

    def test_protocol_double_all_pass_and_repeated_baseline_logged(self):
        self.synthetic_ai()
        summary = self.run_all()
        self.assertTrue(summary["business_verified"])
        self.assertEqual(80, summary["metrics"]["known_tokens"])
        record = self.result_record()
        for case in record["cases"][-2:]:
            self.assertEqual(2, len(case["attempts"]))
            self.assertEqual(2, len(case["baseline"]))
            self.assertEqual(0, case["comparison"]["delta"])

    def test_optional_failure_does_not_block_required_pass(self):
        self.synthetic_ai()
        optional = copy.deepcopy(self.project["tests"][0])
        optional.update(id="optional", required=False, expected={"wrong": True})
        self.project["tests"].append(optional)
        summary = self.run_all()
        self.assertTrue(summary["business_verified"])
        self.assertEqual(1, summary["counts"]["failed"])

    def test_required_wrong_result_prevents_verified(self):
        self.synthetic_ai()
        self.project["tests"][1]["expected"]["total"] = "999.00"
        summary = self.run_all()
        self.assertFalse(summary["business_verified"])
        self.assertEqual(1, summary["counts"]["failed"])

    def test_partial_run_does_not_reuse_previous_pass(self):
        self.synthetic_ai()
        self.run_all()
        summary = creator.run_tests(self.workspace, ["exact-money"])
        self.assertEqual(6, summary["counts"]["not_run"])
        self.assertFalse(summary["business_verified"])
        self.assertEqual(2, len(list((self.workspace / "runs").iterdir())))

    def test_unknown_case_rejected(self):
        self.build()
        with self.assertRaises(creator.ContractError):
            creator.run_tests(self.workspace, ["typo"])

    def test_report_detects_modified_delivery(self):
        self.synthetic_ai()
        summary = self.run_all()
        self.assertTrue(summary["business_verified"])
        pointer = creator.read(self.workspace / "build.json")
        (self.workspace / pointer["skill"] / "SKILL.md").write_text("modified")
        self.assertFalse(creator.report(self.workspace)["business_verified"])
        with self.assertRaises(creator.ContractError):
            creator.build(self.workspace)

    def test_report_detects_changed_source_and_new_build(self):
        self.synthetic_ai()
        self.run_all()
        self.project["version"] = "2.0.0"
        self.save()
        self.assertFalse(creator.report(self.workspace)["business_verified"])
        self.build()
        self.assertFalse(creator.report(self.workspace)["business_verified"])
        self.assertTrue(creator.run_tests(self.workspace)["business_verified"])

    def test_report_detects_tampered_evidence(self):
        self.synthetic_ai()
        summary = self.run_all()
        report_path = Path(summary["report"])
        (report_path.parent / "exact-money/attempts-1/stdout.txt").write_text("tampered")
        summary = creator.report(self.workspace)
        self.assertFalse(summary["business_verified"])
        self.assertTrue(any("证据已改变" in x for x in summary["integrity_issues"]))

    def test_revision_records_previous_run_and_retest(self):
        self.run_all()
        with contextlib.redirect_stdout(io.StringIO()):
            code = creator.main(["revise", "--workspace", str(self.workspace), "--issue", "真实模型不可用", "--change", "记录待接入"])
        self.assertEqual(0, code)
        creator.run_tests(self.workspace)
        record = self.result_record()
        self.assertEqual(1, len(record["revisions"]))
        self.assertIsNotNone(record["revisions"][0]["previous_run"])

    def execute(self, code, test=None):
        case = test or {"mode": "code", "input": {"x": 1}, "expected": {"x": 1}, "timeout": 2}
        return creator.execute(case, [sys.executable, "-c", code], self.workspace, self.workspace,
                               self.workspace / ("attempt-" + str(len(list(self.workspace.iterdir())))))

    def test_invalid_json_fails(self):
        self.assertEqual("failed", self.execute("print('not json')")["status"])

    def test_nonzero_exit_retains_stderr(self):
        result = self.execute("import sys; print('failure',file=sys.stderr); sys.exit(3)")
        self.assertEqual("failed", result["status"])
        self.assertEqual(3, result["returncode"])
        self.assertTrue(any("failure" in p.read_text() for p in self.workspace.rglob("stderr.txt")))

    def test_timeout_is_failed(self):
        self.assertEqual("failed", self.execute("import time; time.sleep(1)", {"mode":"code", "expected":None, "timeout":0.02})["status"])

    def test_missing_executable_is_blocked(self):
        result = creator.execute({"input": None}, [str(self.workspace / "missing")], self.workspace, self.workspace, self.workspace / "blocked")
        self.assertEqual("blocked", result["status"])

    def test_arguments_are_not_shell_evaluated(self):
        text = "$(touch DO_NOT_CREATE); literal"
        result = creator.execute({"mode":"code", "expected":text}, [sys.executable,"-c","import json,sys; print(json.dumps(sys.argv[1]))",text],self.workspace,self.workspace,self.workspace/"literal")
        self.assertEqual("passed", result["status"])
        self.assertFalse((self.workspace / "literal/DO_NOT_CREATE").exists())

    def test_ai_missing_fields_nonfinite_scores_and_false_scores_fail(self):
        base = dict(response="real response placeholder in test", model="synthetic", reviewer="synthetic", evidence="test only", scores={"quality":1}, tokens=None)
        for key in ("response", "model", "reviewer", "evidence", "scores"):
            actual = copy.deepcopy(base)
            actual.pop(key)
            result = self.execute("print(" + repr(json.dumps(actual)) + ")", {"mode":"ai", "rubric":{"quality":0.8}})
            self.assertEqual("failed", result["status"])
        for value in (True, -1, 2, float("nan"), "1"):
            actual = copy.deepcopy(base)
            actual["scores"]["quality"] = value
            result = self.execute("print(" + repr(json.dumps(actual)) + ")", {"mode":"ai", "rubric":{"quality":0.8}})
            self.assertEqual("failed", result["status"])

    def test_baseline_low_score_is_valid_comparison(self):
        self.synthetic_ai()
        baseline = self.workspace / "baseline.py"
        baseline.write_text((self.workspace / "synthetic_ai.py").read_text().replace("'routing':1", "'routing':0"))
        for case in self.project["tests"][-2:]:
            case["baseline"]["command"] = ["{python}","{workspace}/baseline.py"]
        self.assertTrue(self.run_all()["business_verified"])
        self.assertEqual(1, self.result_record()["cases"][-1]["comparison"]["delta"])

    def test_missing_baseline_blocks_otherwise_passing_ai(self):
        self.synthetic_ai()
        self.project["tests"][-1]["baseline"]["command"] = None
        summary = self.run_all()
        self.assertFalse(summary["business_verified"])
        self.assertEqual("blocked", self.result_record()["cases"][-1]["status"])

    def test_numeric_boolean_baseline_is_not_valid_comparison(self):
        self.synthetic_ai()
        baseline = self.workspace / "invalid_baseline.py"
        baseline.write_text((self.workspace / "synthetic_ai.py").read_text().replace("result=x['expected']", "result=int(x['expected'])"))
        for case in self.project["tests"][-2:]:
            case["baseline"]["command"] = ["{python}", "{workspace}/invalid_baseline.py"]
        summary = self.run_all()
        self.assertFalse(summary["business_verified"])
        case = self.result_record()["cases"][-1]
        self.assertEqual("blocked", case["status"])
        self.assertFalse(case["baseline"][0]["protocol_valid"])
        self.assertNotIn("comparison", case)

    def test_valid_but_inaccurate_baseline_remains_comparable(self):
        self.synthetic_ai()
        baseline = self.workspace / "inaccurate_baseline.py"
        baseline.write_text((self.workspace / "synthetic_ai.py").read_text().replace("result=x['expected']", "result=not x['expected']"))
        for case in self.project["tests"][-2:]:
            case["baseline"]["command"] = ["{python}", "{workspace}/inaccurate_baseline.py"]
        self.assertTrue(self.run_all()["business_verified"])
        self.assertEqual("failed", self.result_record()["cases"][-1]["baseline"][0]["status"])

    def test_modified_results_cannot_upgrade_blocked_to_passed(self):
        summary = self.run_all()
        path = Path(summary["report"]).parent / "results.json"
        record = creator.read(path)
        for case in record["cases"]:
            case["status"] = "passed"
        creator.dump(path, record)
        summary = creator.report(self.workspace)
        self.assertFalse(summary["business_verified"])
        self.assertTrue(any("结果记录已改变" in x for x in summary["integrity_issues"]))

    def test_exact_json_distinguishes_boolean_and_number(self):
        result = self.execute("print('{\"x\": true}')")
        self.assertEqual("failed", result["status"])

    def test_report_before_tests_has_actionable_message(self):
        with self.assertRaisesRegex(creator.ContractError, "先完成 check、build"):
            creator.report(self.workspace)

    def test_test_resources_change_invalidates_report(self):
        self.project["test_resources"] = ["check_structure.py"]
        self.run_all()
        adapter = self.workspace / "check_structure.py"
        adapter.write_text(adapter.read_text() + "\n# modified\n")
        self.assertFalse(creator.report(self.workspace)["static_passed"])
        with self.assertRaises(creator.ContractError):
            creator.run_tests(self.workspace)

    def test_nested_reference_links_checked(self):
        (self.workspace / "notes.md").write_text("[missing](missing.md)")
        self.project["resources"].append({"source":"notes.md", "target":"references/notes.md"})
        with self.assertRaisesRegex(creator.ContractError, "引用不存在"):
            self.build()

    def test_malformed_field_types_fail_closed(self):
        for category, field, value in (("requirements", "status", []), ("nodes", "mode", {}), ("tests", "layer", [])):
            old = self.project[category][0][field]
            self.project[category][0][field] = value
            self.assertTrue(self.errors())
            self.project[category][0][field] = old

    def test_delivery_symlink_escape_rejected(self):
        with tempfile.TemporaryDirectory() as external:
            (self.workspace / "delivery").symlink_to(external)
            with self.assertRaisesRegex(creator.ContractError, "越界"):
                self.build()
            self.assertEqual([], list(Path(external).iterdir()))

    def test_cli_exit_codes_and_unicode_paths(self):
        target = self.workspace / "中文 空格"
        result = subprocess.run([sys.executable,str(SCRIPT),"init","--workspace",str(target),"--name","demo"],capture_output=True,text=True)
        self.assertEqual(0, result.returncode)
        result = subprocess.run([sys.executable,str(SCRIPT),"check","--workspace",str(target)],capture_output=True,text=True)
        self.assertEqual(1, result.returncode)
        result = subprocess.run([sys.executable,str(SCRIPT),"build","--workspace",str(target)],capture_output=True,text=True)
        self.assertEqual(2, result.returncode)


class BusinessExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("summarize", EXAMPLE / "summarize.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_rejects_invalid_candidates_and_amounts(self):
        for mapping in ({}, {"amount":"price"}, {"amount":"price","status":"price"}, {"amount":[],"status":"state"}):
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                self.module.summarize({"orders":[],"mapping":mapping})
        for amount in ("NaN", "Infinity", "-1.00", "1.001", "not money", 1, True):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                self.module.summarize({"orders":[{"price":amount,"state":"paid"}],"mapping":{"amount":"price","status":"state"}})

    def test_unknown_status_and_missing_column_rejected(self):
        for row in ({"price":"1","state":"unknown"}, {"price":"1"}):
            with self.assertRaises(ValueError):
                self.module.summarize({"orders":[row],"mapping":{"amount":"price","status":"state"}})

    def test_exact_paid_sum_and_cancelled_exclusion(self):
        actual = self.module.summarize({"orders":[{"price":"0.10","state":"paid"},{"price":"0.20","state":"paid"},{"price":"100.00","state":"cancelled"}],"mapping":{"amount":"price","status":"state"}})
        self.assertEqual({"paid_count":2,"total":"0.30"},actual)

    def test_large_totals_do_not_round_at_decimal_context_precision(self):
        actual = self.module.summarize({"orders":[{"price":"9999999999999999999999999999.99","state":"paid"},
                                                 {"price":"0.01","state":"paid"}],
                                        "mapping":{"amount":"price","status":"state"}})
        self.assertEqual("10000000000000000000000000000.00", actual["total"])


if __name__ == "__main__":
    unittest.main()
