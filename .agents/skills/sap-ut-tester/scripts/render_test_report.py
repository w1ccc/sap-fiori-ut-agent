"""Create or update a conversation's Excel report from saved execution manifests."""

import argparse
from collections import defaultdict
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName


OWNER = "SAP_UT_REPORT_OWNER"
COLORS = {"PASS": "CAEDD9", "FAIL": "F7C7C8", "BLOCKED": "E2E6EB",
          "PENDING_EXTERNAL": "FFE7AC", "SKIPPED": "E2E6EB", "NOT EXECUTED": "EEF1F4"}
CASE_STATUSES = {"PASS", "FAIL", "BLOCKED", "PENDING_EXTERNAL"}
OVERALL = ["Case ID", "Description", "Module", "Steps", "Passed", "Status"]
DETAIL = ["Step", "Description", "Module", "Instructions", "Application",
          "Expected", "Actual", "SAP Error", "Result"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sheet_name(case_id):
    return re.sub(r"[\\/:?*\[\]]", "_", case_id)[:31].strip("'")


def put_row(sheet, number, values):
    for column, value in enumerate(values, 1):
        cell = sheet.cell(number, column)
        cell.value = value
        if isinstance(value, str):
            cell.data_type = "s"  # Preserve source text, including literal spreadsheet formulas.
        cell.alignment = Alignment(vertical="top", wrap_text=True)


def layout(sheet, title, headers, widths):
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(headers))
    put_row(sheet, 1, [title])
    sheet["A1"].font = Font(size=14, bold=True, color="233955")
    sheet.row_dimensions[1].height = 24
    put_row(sheet, 2, headers)
    for cell, width in zip(sheet[2], widths):
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="315577")
        sheet.column_dimensions[get_column_letter(cell.column)].width = width
    sheet.freeze_panes = "A3"


def write_case(sheet, entry, contract, run):
    steps = contract["steps"]
    keys_by_row = defaultdict(list)
    known = set()
    for step in steps:
        key = step["step_key"]
        address = key["sheet"], key["row"]
        require(address[0] and type(address[1]) is int and address[1] > 0
                and address not in known, "Invalid or duplicate contract step key")
        require(str(step.get("module_raw", step.get("module_normalized", ""))).strip().upper() == "STP",
                "Contract contains an out-of-scope step")
        known.add(address)
        keys_by_row[address[1]].append(address)

    results = {}
    for result in (run or {}).get("steps", []):
        key = result.get("step_key", result)
        matches = [(key["sheet"], key["row"])] if key.get("sheet") else keys_by_row[key["row"]]
        require(len(matches) == 1 and matches[0] in known and matches[0] not in results,
                "Unknown, ambiguous or duplicate result step")
        results[matches[0]] = result
    status = "NOT STARTED" if run is None else run.get("execution_status", run.get("status"))
    require(run is None or status in CASE_STATUSES, "Invalid case execution status")
    descriptions = {s.get("description") for s in steps}
    name = contract.get("case_name") or (descriptions.pop() if len(descriptions) == 1 else "") or ""
    layout(sheet, f"{entry['case_id']} {name} — {status}".replace("  —", " —"), DETAIL,
           [11, 28, 12, 55, 25, 36, 47, 38, 22])
    if status in COLORS:
        sheet.sheet_properties.tabColor = COLORS[status]
    passed = 0
    for i, step in enumerate(steps, 3):
        key = step["step_key"]
        result = results.get((key["sheet"], key["row"]), {})
        verdict = result.get("result")
        if verdict is None:
            state = result.get("status", "NOT EXECUTED")
            require(state in {"NOT EXECUTED", "NOT_EXECUTED", "PENDING_EXTERNAL"},
                    "An executed step must have an explicit result")
            verdict = "PENDING_EXTERNAL" if state == "PENDING_EXTERNAL" else "NOT EXECUTED"
        require(verdict in COLORS and (verdict != "PENDING_EXTERNAL" or status == verdict),
                "Invalid step result for the case state")
        errors = [m["text"] for m in result.get("messages", [])
                  if m.get("source") == "SAP" and m.get("severity") == "ERROR" and not m.get("resolved")]
        instructions = "\n".join(f"{o['operation_no']}. {o['raw_text']}" for o in step["operations"])
        put_row(sheet, i, [step.get("step_count"), step.get("description"), "STP", instructions,
                           step["app"].get("raw_name"), step.get("expected_result_raw"),
                           result.get("actual_result"), "\n".join(errors), verdict])
        sheet.cell(i, 9).fill = PatternFill("solid", fgColor=COLORS[verdict])
        passed += verdict == "PASS"
    return [entry["case_id"], name, "STP", len(steps), passed, status]


@contextmanager
def report_lock(path):
    # The OS releases the lock if a renderer exits before completing a write.
    with path.with_suffix(".xlsx.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def render(mode, manifest_file, case_id=None, run_file=None):
    manifest = load_json(manifest_file)
    output = Path(manifest["report_path"]).resolve()
    require(output.is_relative_to((Path.cwd() / "results").resolve()) and output.suffix.lower() == ".xlsx",
            "Report must be an XLSX file under the project's results directory")
    environment = manifest.get("environment") or {}
    require(manifest["conversation_id"] and manifest["report_started_at"], "Missing report owner")
    identity = [manifest["conversation_id"], manifest["report_started_at"],
                environment.get("classification"), str(environment.get("client", ""))]
    marker = '"' + sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest() + '"'
    entries = manifest["case_entries"]
    ids = [e["case_id"].strip().casefold() for e in entries]
    names = [sheet_name(e["case_id"]).casefold() for e in entries]
    require(entries and all(ids) and all(names) and len(set(ids)) == len(ids)
            and len(set(names)) == len(names) and "overall" not in names, "Conflicting case identifiers")
    selected = case_id.strip().casefold() if case_id else None
    require(not selected or (mode == "update" and selected in ids), "Invalid --case-id")
    require(not run_file or selected, "--run-manifest requires --case-id")
    output.parent.mkdir(parents=True, exist_ok=True)
    with report_lock(output):
        if mode == "initialize":
            require(not output.exists(), "Report already exists")
            book = Workbook()
            overall = book.active
            overall.title = "Overall"
            layout(overall, "Test execution", OVERALL, [25, 48, 15, 13, 13, 24])
            book.defined_names.add(DefinedName(OWNER, attr_text=marker, hidden=True))
        else:
            book = load_workbook(output)
            owner = book.defined_names.get(OWNER)
            require(owner and owner.attr_text == marker, "Report belongs to a different session or environment")
            overall = book["Overall"]
        previous = {}
        for row in range(3, overall.max_row + 1):
            stored = str(overall.cell(row, 1).value).strip().casefold()
            require(stored in ids and stored not in previous, "Overall includes an unrelated or duplicate case")
            previous[stored] = row
        changed = []
        for entry, identifier in zip(entries, ids):
            apply_run = mode == "update" and (selected is None or selected == identifier)
            if identifier in previous and not apply_run:
                continue
            contract = entry.get("execution_contract") or load_json(entry["contract_ref"])
            expected_sha = contract.get("source", {}).get("sha256")
            entry_sha = entry.get("source", {}).get("sha256")
            require(not (expected_sha and entry_sha) or expected_sha == entry_sha, "Contract source mismatch")
            run = None
            if apply_run:
                if run_file:
                    run = load_json(run_file)
                elif "run_manifest" in entry:
                    run = entry["run_manifest"]
                elif "run_manifest_ref" in entry:
                    run = load_json(entry["run_manifest_ref"])
                else:
                    require(Path(entry["case_id"]).name == entry["case_id"] and entry["case_id"] not in {".", ".."},
                            "This case needs an explicit run manifest")
                    run = load_json(Path(manifest_file).parent / entry["case_id"] / "run-manifest.json")
                require(run["case_id"].strip().casefold() == identifier and run["conversation_id"] == identity[0],
                        "Run belongs to a different case or conversation")
                for key in ("client", "classification"):
                    if environment.get(key) is not None:
                        require(str(run.get("environment", {}).get(key)) == str(environment[key]),
                                f"Run environment mismatch: {key}")
                run_sha = run.get("source", {}).get("sha256")
                require(not (run_sha and (entry_sha or expected_sha)) or run_sha == (entry_sha or expected_sha),
                        "Run source mismatch")
            title = sheet_name(entry["case_id"])
            if identifier in previous:
                row = previous[identifier]
                old = book[sheet_name(overall.cell(row, 1).value)]
                index = book.index(old)
                book.remove(old)
            else:
                row = overall.max_row + 1
                index = len(book.sheetnames)
            summary = write_case(book.create_sheet(title, index), entry, contract, run)
            put_row(overall, row, summary)
            if summary[-1] in COLORS:
                overall.cell(row, 6).fill = PatternFill("solid", fgColor=COLORS[summary[-1]])
            changed.append(entry["case_id"])
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".xlsx", delete=False) as tmp:
            staged = Path(tmp.name)
        try:
            book.save(staged)
            with staged.open("rb") as handle:
                check = load_workbook(handle, read_only=True)
                try:
                    require({s.casefold() for s in check.sheetnames} == {"overall", *names},
                            "Saved worksheet list differs from the manifest")
                finally:
                    check.close()
            os.replace(staged, output)
        finally:
            staged.unlink(missing_ok=True)
            book.close()
    return {"report_status": "SUCCESS", "excel_path": str(output), "updated_cases": changed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("initialize", "update"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--run-manifest", type=Path)
    args = parser.parse_args()
    try:
        result = render(args.mode, args.manifest, args.case_id, args.run_manifest)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"report_status": "FAILURE", "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
