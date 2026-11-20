"""Run the public command-line tools against invented records in a temporary directory."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent


def run_json(script, *args):
    output = subprocess.check_output(
        [sys.executable, "-B", "-X", "utf8", str(SCRIPTS / script), *map(str, args)],
        encoding="utf-8",
    )
    return json.loads(output)


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def verify(tmp):
    workbook = tmp / "demo.xlsx"
    subprocess.run([sys.executable, "-B", str(SCRIPTS.parent / "tests" / "make_demo_workbook.py"),
                    str(workbook)], check=True)
    raw = run_json("build_test_contract.py", workbook, "--case-id", "CASE_001", "--run-id", "demo-run")
    assert raw["scope"]["selection"]["selected_rows"] == [{"sheet": "Steps", "row": 2}]
    raw_file = save(tmp / "raw.json", raw)
    key = {"sheet": "Steps", "row": 2}
    ref = "Test Data!R2C2"
    bindings = {
        "run_id": "demo-run", "source_sha256": raw["source"]["sha256"],
        "steps": [{"step_key": key,
                   "app": {"app_id": "demo.viewer", "route": "demo-display", "surface": "SAP_FIORI"},
                   "required_fields": ["Document"], "evidence_ref": "demo-observation",
                   "expected_state": "detail visible",
                   "readonly_search": {"input_field": "Document", "search_control": "demo-search",
                                       "readonly_target": "demo-display", "record_identity_fields": ["Document"]}}],
    }
    bindings_file = tmp / "bindings.json"
    missing = run_json("compile_execution_input.py", raw_file, "--bindings", save(bindings_file, bindings))
    plan = missing["execution_contract"]["steps"][0]["compiled_plan"]
    assert plan["status"] == "NEED_DECISION" and "NEED_SOURCED_INPUT:Document" in plan["missing"]
    bindings.update(authorization_ref="demo-request", authorized_source_refs=[ref])
    ready = run_json("compile_execution_input.py", raw_file, "--bindings", save(bindings_file, bindings))
    assert ready["execution_contract"]["readonly_plan"]["search_value"] == "DEMO-DOCUMENT"
    assert ready["execution_contract"]["readonly_plan"]["search_source_ref"] == ref
    assert ready == run_json("compile_execution_input.py", raw_file, "--bindings", bindings_file)

    approval = run_json("build_test_contract.py", workbook, "--case-id", "CASE_002", "--run-id", "demo-approval")
    assert approval["steps"][0]["external_systems"] == ["Example approval service"]
    status = save(tmp / "session.json", {"status": "FAILURE"})
    checkpoint = {"checkpoint_id": "demo-checkpoint", "case_id": "CASE_002", "next_step_index": 1,
                  "active_output_refs": ["demo-output"]}
    resumed = run_json("route_session_failure.py", "RESUME_EXECUTION", status,
                       "--checkpoint", save(tmp / "checkpoint.json", checkpoint))
    assert resumed["status"] == "PAUSED_FOR_INTERVENTION" and resumed["verified_recovery_checkpoint"] == checkpoint
    manifest = save(tmp / "manifest.json", {"case_entries": [
        {"case_id": "CASE_002"}, {"case_id": "CASE_001", "depends_on_cases": [], "prerequisites_ready": True}
    ]})
    state = {"case_id": "CASE_002", "execution_status": "PENDING_EXTERNAL", "report_status": "SUCCESS",
             "case_stop_scope": "CASE_LOCAL", "verified_recovery_checkpoint": checkpoint}
    decision = run_json("select_next_case.py", manifest, "--case-id", "CASE_002", "--case-id", "CASE_001",
                        "--result", save(tmp / "result.json", state))
    assert decision == {"status": "NEXT_CASE", "case_id": "CASE_001"}


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="fiori-ut-demo-") as directory:
        verify(Path(directory))
    print("PASS: scope, sourced input, repeatable plan, external dependencies, recovery and case scheduling")
