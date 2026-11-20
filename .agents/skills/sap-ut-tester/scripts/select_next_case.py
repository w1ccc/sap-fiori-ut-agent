"""Record one Case boundary and choose the next authorized Case in request order."""

import argparse
import json
import os
import tempfile
from pathlib import Path


TERMINAL = {"PASS", "FAIL", "BLOCKED"}


def case_key(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Expected a Case ID")
    return value.strip().casefold()


def select_next_case(entries: list[dict], requested: list[str]) -> dict:
    by_id = {case_key(entry["case_id"]): entry for entry in entries}
    if len(by_id) != len(entries) or not requested:
        raise ValueError("Expected unique manifest cases and a non-empty request")
    selected = [by_id[case_key(case_id)] for case_id in requested]
    pending = []
    for entry in entries:
        status = entry.get("execution_status", "NOT STARTED")
        if entry.get("report_status") == "FAILURE":
            return {"status": "REPORT_FAILURE", "case_id": entry["case_id"]}
        if entry.get("case_stop_scope") == "BATCH_INTERVENTION_REQUIRED":
            return {"status": "INTERVENTION_REQUIRED", "case_id": entry["case_id"]}
        if status not in TERMINAL | {"PENDING_EXTERNAL", "NOT STARTED"}:
            return {"status": "INTERVENTION_REQUIRED", "case_id": entry["case_id"]}
        if status != "NOT STARTED" and entry.get("report_status") != "SUCCESS":
            return {"status": "REPORT_FAILURE", "case_id": entry["case_id"]}
        if status != "NOT STARTED" and entry.get("case_stop_scope") not in {"CASE_LOCAL", "CASE_TERMINAL"}:
            return {"status": "INTERVENTION_REQUIRED", "case_id": entry["case_id"]}
        if status == "PENDING_EXTERNAL":
            if not entry.get("verified_recovery_checkpoint"):
                return {"status": "INTERVENTION_REQUIRED", "case_id": entry["case_id"]}
            pending.append(entry["case_id"])

    for entry in selected:
        if entry.get("execution_status", "NOT STARTED") != "NOT STARTED":
            continue
        dependencies = entry.get("depends_on_cases")
        if pending and (not isinstance(dependencies, list) or entry.get("prerequisites_ready") is not True):
            continue
        if dependencies is not None and (
            not isinstance(dependencies, list)
            or any(by_id.get(case_key(dep), {}).get("execution_status") != "PASS" for dep in dependencies)
        ):
            continue
        if entry.get("prerequisites_ready") is False:
            continue
        return {"status": "NEXT_CASE", "case_id": entry["case_id"]}

    if pending:
        return {"status": "PENDING_EXTERNAL", "pending_case_ids": pending}
    if all(entry.get("execution_status") in TERMINAL for entry in selected):
        return {"status": "ALL_CASES_COMPLETE"}
    return {"status": "INTERVENTION_REQUIRED"}


def record_case(entries: list[dict], requested: list[str], result: dict) -> None:
    run = result.get("current_run_manifest") or result
    case_id = case_key(run["case_id"])
    matches = [entry for entry in entries if case_key(entry["case_id"]) == case_id]
    if len(matches) != 1 or case_id not in map(case_key, requested):
        raise ValueError("Child result must belong to exactly one requested Case")
    entry = matches[0]
    state = result["execution_status"]
    if state not in TERMINAL | {"PENDING_EXTERNAL"} or run.get("execution_status", state) != state:
        raise ValueError("Child output and run manifest must identify the same execution status")
    checkpoint = result.get("verified_recovery_checkpoint")
    if state == "PENDING_EXTERNAL" and not checkpoint:
        raise ValueError("Pending external Case requires its verified checkpoint")
    if checkpoint is not None:
        if not isinstance(checkpoint, dict) or not checkpoint.get("checkpoint_id"):
            raise ValueError("Invalid verified checkpoint")
        if checkpoint.get("case_id") and case_key(checkpoint["case_id"]) != case_id:
            raise ValueError("Checkpoint belongs to another Case")
        if checkpoint.get("source_sha256") and entry.get("source", {}).get("sha256") not in {None, checkpoint["source_sha256"]}:
            raise ValueError("Checkpoint belongs to another source version")
        if checkpoint.get("contract_run_id") and run.get("run_id") not in {None, checkpoint["contract_run_id"]}:
            raise ValueError("Checkpoint belongs to another execution of this Case")
    entry.update(execution_status=state, report_status=result["report_status"], case_stop_scope=result["case_stop_scope"])
    if checkpoint:
        entry["verified_recovery_checkpoint"] = checkpoint
    elif state in TERMINAL:
        entry.pop("verified_recovery_checkpoint", None)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--case-id", action="append", required=True, help="Requested Case ID in execution order")
    parser.add_argument("--result", type=Path, help="Persist the child Workflow output after its report update")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        entries = manifest["case_entries"]
        if args.result:
            record_case(entries, args.case_id, json.loads(args.result.read_text(encoding="utf-8-sig")))
        decision = select_next_case(entries, args.case_id)
        if args.result:
            with tempfile.NamedTemporaryFile(dir=args.manifest.parent, delete=False) as handle:
                staged = Path(handle.name)
            try:
                staged.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                os.replace(staged, args.manifest)
            finally:
                staged.unlink(missing_ok=True)
    except (OSError, KeyError, ValueError, TypeError) as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(decision))


if __name__ == "__main__":
    main()
