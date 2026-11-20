#!/usr/bin/env python3
"""Build a provenance-preserving STP test contract from an XLSX source.

The script is intentionally read-only with respect to the source workbook. It
does not authorize values, infer missing expected results, or resolve business
ambiguity. Those cases are emitted as structured issues for the Workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, time, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

try:
    from openpyxl import load_workbook
except ImportError as exc:  # pragma: no cover - exercised by the runtime gate
    raise SystemExit("openpyxl is required to read XLSX test sources") from exc


CANONICAL_HEADERS = {
    "businessstepid": "business_step_id",
    "businessstepdescription": "business_step_description",
    "module": "module",
    "scenariocaseid": "case_id",
    "caseid": "case_id",
    "testdescriptiondetails": "test_description",
    "stepcount": "step_count",
    "stepdescription": "step_description",
    "sapfioriapplicationname": "application",
    "application": "application",
    "expectedresult": "expected_result",
    "dependson": "dependency_reference",
    "dependencies": "dependency_reference",
    "externalsystem": "external_system",
    "tester": "tester",
    "actualtestdate": "actual_test_date",
    "outputdataactualresult": "historical_output",
    "statuspassinprogressfail": "historical_status",
}

ID_PATTERN = re.compile(r"\b(?:CASE|TEST|SIT|UAT)[\s_-]*\d+\b", re.IGNORECASE)
NUMBERED_OPERATION = re.compile(r"(?ms)^\s*(\d+)\s*[.)]\s*(.*?)(?=^\s*\d+\s*[.)]\s*|\Z)")

# Match an imperative or a named control. Business nouns (e.g. "goods
# receipt", "posting date") and past-tense observations do not identify actions.
VERB_EFFECTS = {
    "delete": "DELETE", "mark for deletion": "DELETE", "approve": "APPROVE",
    "release": "APPROVE", "post": "POST", "send": "SEND", "submit": "SEND",
    "create": "CREATE", "save": "UPDATE", "park": "UPDATE",
    "change": "UPDATE", "close pr": "UPDATE", "fill": "DRAFT", "input": "DRAFT",
    "upload": "DRAFT", "assign": "DRAFT",
}
WRITE_COMMAND = re.compile(
    r"(?:^|[,，;.]\s*|\b(?:and|then)\s+)(?:then\s+)?"
    r"(?:(?:click|press|choose)\s+(?:on\s+)?(?:the\s+)?[\"'“‘]?)?"
    r"(" + "|".join(VERB_EFFECTS) + r")\b", re.I,
)
READ_COMMAND = re.compile(
    r"(?:go into (?:the )?app|open (?:the )?(?:app|application)|click [\"']?go[\"']?(?: button)?|"
    r"(?:review|search|verify|display|inspect|filter|find|check)\s+[^,;.!?]+)", re.I,
)
COMPOUND_READ = re.compile(r"\b(?:and|then|before|after|unless|to)\b|[,，;\n]", re.I)
ORDER_BUTTON = re.compile(r"\b(?:click|press)\s+(?:the\s+)?[\"'“‘]?order\b", re.I)


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"[^0-9a-z]+", "", str(value).casefold())


def normalize_module(value: Any) -> str:
    return "" if value is None else str(value).strip().upper()


def normalize_case_id(value: Any) -> str:
    return "" if value is None else str(value).strip().casefold()


def json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value


def detect_ids(values: Iterable[Any]) -> list[str]:
    found: list[str] = []
    for value in values:
        if value is None:
            continue
        for match in ID_PATTERN.findall(str(value)):
            normalized = re.sub(r"[\s-]+", "_", match.upper())
            if normalized not in found:
                found.append(normalized)
    return found


def find_header_rows(worksheet: Any) -> list[tuple[int, dict[str, int]]]:
    candidates: list[tuple[int, dict[str, int]]] = []
    for row_number in range(1, min(worksheet.max_row, 50) + 1):
        mapping: dict[str, int] = {}
        for column in range(1, worksheet.max_column + 1):
            raw = worksheet.cell(row_number, column).value
            canonical = CANONICAL_HEADERS.get(normalize_header(raw))
            if canonical and canonical not in mapping:
                mapping[canonical] = column
        if "module" in mapping and "step_description" in mapping:
            candidates.append((row_number, mapping))
    return candidates


def classify_operation(text: str) -> tuple[str, list[str]]:
    statement = text.strip().rstrip(".").strip()
    if re.fullmatch(r"click (?:the )?create purchase order application", statement, re.I):
        return "READ", [statement]
    commands = [match.group(1).lower() for match in WRITE_COMMAND.finditer(text)]
    effects = {VERB_EFFECTS[command] for command in commands}
    if ORDER_BUTTON.search(text):
        commands.append("click order")
        effects.add("CREATE")
    commits = effects - {"DRAFT"}
    if len(commits) == 1:
        return commits.pop(), commands
    if effects == {"DRAFT"}:
        return "DRAFT", commands
    if not effects:
        # A known read verb must describe the whole action, not the beginning
        # of a compound instruction containing a second, unrecognised effect.
        if READ_COMMAND.fullmatch(statement) and not COMPOUND_READ.search(statement):
            return "READ", [statement]
    return "UNKNOWN", commands


def split_operations(raw_text: Any) -> list[dict[str, Any]]:
    text = "" if raw_text is None else str(raw_text).strip()
    if not text:
        return []
    matches = list(NUMBERED_OPERATION.finditer(text))
    chunks = [(match.group(1), match.group(2).strip()) for match in matches]
    if not chunks:
        chunks = [(1, text)]
    operations: list[dict[str, Any]] = []
    for operation_no, chunk in chunks:
        effect_class, triggers = classify_operation(chunk)
        operations.append(
            {
                "operation_no": int(operation_no),
                "raw_text": chunk,
                "effect_class": effect_class,
                "effect_triggers": triggers,
            }
        )
    return operations


def issue(code: str, disposition: str, message: str, *source_refs: str) -> dict[str, Any]:
    return {
        "code": code,
        "disposition": disposition,
        "message": message,
        "source_refs": [ref for ref in source_refs if ref],
    }


def cell_input(cell: Any, cached_sheet: Any) -> dict[str, Any]:
    """Keep the literal/formula and the cached value from the very same XLSX bytes."""
    formula = cell.value if cell.data_type == "f" else None
    value_cell = cached_sheet[cell.coordinate] if formula else cell
    value = json_value(value_cell.value) if value_cell.data_type != "e" else None
    display = value
    if isinstance(value, int) and not isinstance(value, bool) and re.fullmatch(r"0{2,}", cell.number_format):
        display = format(value, f"0{len(cell.number_format)}d")
    return {
        "value": value, "formula": formula, "display_value": display,
        "data_type": type(value_cell.value).__name__ if value is not None else None,
        "source_ref": f"{cell.parent.title}!{cell.coordinate}",
    }


def extract_test_data(workbook: Any, cached: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for worksheet in workbook.worksheets:
        for row_number in range(1, min(worksheet.max_row, 50) + 1):
            headers = [normalize_header(worksheet.cell(row_number, col).value) for col in range(1, worksheet.max_column + 1)]
            if "testdata" not in headers or "value" not in headers:
                continue
            name_col = headers.index("testdata") + 1
            value_col = headers.index("value") + 1
            for data_row in range(row_number + 1, worksheet.max_row + 1):
                name = worksheet.cell(data_row, name_col).value
                cell = worksheet.cell(data_row, value_col)
                if name in (None, "") and cell.value in (None, ""):
                    continue
                field = cell_input(cell, cached[worksheet.title])
                records.append(
                    {
                        "name": "" if name is None else str(name).strip(),
                        "raw_value": json_value(cell.value),
                        "formula": field["formula"],
                        "display_value": field["display_value"],
                        "canonical_value": field["display_value"],
                        "data_type": field["data_type"],
                        "unit": None,
                        "source_ref": f"{worksheet.title}!R{data_row}C{value_col}",
                        "source_kind": "source_test_data",
                        "authorized_for_run": False,
                    }
                )
            break
    return records


def build_contract(
    path: Path, requested_case_id: str | None = None, run_id: str | None = None, *,
    scope_mode: str = "row", scope_source: str | None = None, source_identity_path: Path | None = None,
) -> dict[str, Any]:
    resolved = path.resolve()
    if scope_mode not in {"row", "workbook"}:
        raise ValueError("scope_mode must be row or workbook")
    if scope_mode == "workbook" and not (normalize_case_id(requested_case_id) and (scope_source or "").strip()):
        raise ValueError("Workbook scope needs a requested workbook ID and an explicit scope decision reference")
    filename_ids = detect_ids([(source_identity_path or resolved).stem])
    if resolved.suffix.casefold() != ".xlsx":
        return {
            "status": "NEED_DECISION",
            "contract_version": 1,
            "run_id": run_id or "unresolved-source",
            "source": {"path": str(resolved), "sha256": None, "source_kind": "file", "sheet": None, "header_row": None},
            "identities": {"requested_case_id": requested_case_id, "filename_ids": detect_ids([resolved.stem]), "sheet_ids": [], "row_case_ids": []},
            "scope": {"module_required": "STP", "surface_required": "SAP_FIORI", "excluded_modules": []},
            "environment": {"host": None, "client": None, "classification": None, "user": None, "evidence_ref": None},
            "authorization": {"effect_classes": ["READ"], "objects": [], "granted_by": None, "granted_at": None, "expires_at": None},
            "test_data": [],
            "steps": [],
            "issues": [issue("UNSUPPORTED_SOURCE_FOR_SCRIPT", "NEED_DECISION", "The deterministic parser supports XLSX only; the Agent must build the same contract schema for this source type.", str(resolved))],
        }

    try:
        source_bytes = resolved.read_bytes()
        sha256 = hashlib.sha256(source_bytes).hexdigest().upper()
        workbook = load_workbook(BytesIO(source_bytes), data_only=False)
        cached = load_workbook(BytesIO(source_bytes), data_only=True)
    except PermissionError:
        return {
            "status": "NEED_DECISION",
            "contract_version": 1,
            "run_id": run_id or "source-locked",
            "source": {"path": str(resolved), "sha256": None, "source_kind": "xlsx", "sheet": None, "header_row": None},
            "identities": {"requested_case_id": requested_case_id, "filename_ids": detect_ids([resolved.stem]), "sheet_ids": [], "row_case_ids": []},
            "scope": {"module_required": "STP", "surface_required": "SAP_FIORI", "excluded_modules": []},
            "environment": {"host": None, "client": None, "classification": None, "user": None, "evidence_ref": None},
            "authorization": {"effect_classes": ["READ"], "objects": [], "granted_by": None, "granted_at": None, "expires_at": None},
            "test_data": [],
            "steps": [],
            "issues": [issue("SOURCE_LOCKED", "NEED_DECISION", "The source is locked. Supply an explicitly verified immutable snapshot and record its relationship to the source.", str(resolved))],
        }

    rows: list[dict[str, Any]] = []
    excluded_modules: set[str] = set()
    header_tables: list[dict[str, Any]] = []
    sheet_ids: list[str] = []
    for worksheet in workbook.worksheets:
        header_candidates = find_header_rows(worksheet)
        for index, (header_row, columns) in enumerate(header_candidates):
            sheet_ids.extend(detect_ids(worksheet.cell(r, c).value for r in range(1, header_row) for c in range(1, worksheet.max_column + 1)))
            header_tables.append({"sheet": worksheet.title, "header_row": header_row})
            end_row = header_candidates[index + 1][0] if index + 1 < len(header_candidates) else worksheet.max_row + 1
            for row_number in range(header_row + 1, end_row):
                module_raw = worksheet.cell(row_number, columns["module"]).value
                module_normalized = normalize_module(module_raw)
                if module_normalized != "STP":
                    if module_normalized:
                        excluded_modules.add(module_normalized)
                    continue
                fields = {key: cell_input(worksheet.cell(row_number, col), cached[worksheet.title]) for key, col in columns.items()}
                values = {key: field["value"] for key, field in fields.items()}
                if "case_id" in fields:
                    values["case_id"] = fields["case_id"]["display_value"]
                rows.append(
                    {
                        "source_ref": f"{worksheet.title}!{row_number}",
                        "sheet": worksheet.title,
                        "row": row_number,
                        "fields": fields,
                        "values": values,
                    }
                )

    all_row_case_ids = sorted({str(row["values"].get("case_id")).strip() for row in rows if row["values"].get("case_id") not in (None, "")}, key=str.casefold)
    requested_normalized = normalize_case_id(requested_case_id)
    selected_rows = rows
    unique_sheet_ids = list(dict.fromkeys(sheet_ids))
    if scope_mode == "workbook":
        workbook_ids = filename_ids or unique_sheet_ids
        if {normalize_case_id(value) for value in workbook_ids} != {requested_normalized}:
            selected_rows = []
    elif requested_normalized:
        selected_rows = [row for row in rows if normalize_case_id(row["values"].get("case_id")) == requested_normalized]

    issues: list[dict[str, Any]] = []
    if not rows:
        issues.append(issue("NO_STP_ROWS", "BLOCKED", "No detail row has an authoritative Module value exactly equal to STP.", str(resolved)))
    elif scope_mode == "workbook" and not selected_rows:
        issues.append(issue("WORKBOOK_ID_MISMATCH", "BLOCKED", "The requested workbook ID does not match the source workbook identity.", str(resolved)))
    elif requested_normalized and not selected_rows:
        issues.append(issue("REQUESTED_CASE_NOT_FOUND", "BLOCKED", "No exact STP row matches the requested case ID.", str(resolved)))
    elif not requested_normalized and len(all_row_case_ids) > 1:
        issues.append(issue("MULTIPLE_CASE_IDS", "NEED_DECISION", "Multiple STP case IDs are present; the user or Agent must choose one explicitly.", *[row["source_ref"] for row in rows]))

    identity_values = {value.casefold() for value in filename_ids + unique_sheet_ids + all_row_case_ids}
    if scope_mode == "row" and len(identity_values) > 1:
        issues.append(issue("CASE_IDENTITY_CONFLICT", "NEED_DECISION", "Filename, sheet title, or row-level case identities conflict. Preserve each identity and do not merge them automatically.", str(resolved)))

    seen_step_counts: dict[str, str] = {}
    contract_steps: list[dict[str, Any]] = []
    for row in selected_rows:
        values = row["values"]
        source_ref = row["source_ref"]
        case_id = values.get("case_id")
        step_count = values.get("step_count")
        step_description = values.get("step_description")
        application = values.get("application")
        expected_result = values.get("expected_result")
        if case_id in (None, ""):
            issues.append(issue("MISSING_CASE_ID", "NEED_DECISION", "An executable STP row has no case ID.", source_ref))
        if step_description in (None, ""):
            issues.append(issue("MISSING_STEP_DESCRIPTION", "NEED_DECISION", "An executable STP row has no Step Description.", source_ref))
        if application in (None, "", "/") or not str(application).strip():
            issues.append(issue("MISSING_FIORI_APPLICATION", "NEED_DECISION", "An executable STP row has no usable SAP Fiori application identity.", source_ref))
        if expected_result in (None, "") or not str(expected_result).strip():
            issues.append(issue("MISSING_EXPECTED_RESULT", "NEED_DECISION", "An executable STP row has no Expected Result; no assertion may be invented.", source_ref))
        step_key = str(step_count).strip().casefold() if step_count not in (None, "") else ""
        if step_key:
            if step_key in seen_step_counts:
                disposition = "NEED_DECISION"
                if scope_mode == "workbook":
                    # The explicit ordered worksheet/row selection identifies both
                    # rows unless an instruction uses their shared number as a key.
                    number = re.compile(rf"(?<!\w){re.escape(step_key)}(?!\w)", re.I)
                    named_step = re.compile(rf"(?:\bsteps?(?:\s+no\.?)?|步骤)\s*[#：:]?\s*{re.escape(step_key)}(?!\w)", re.I)
                    ambiguous = any(
                        number.search(str(item["values"].get("dependency_reference") or ""))
                        or named_step.search(str(item["values"].get("step_description") or ""))
                        for item in selected_rows
                    )
                    if not ambiguous:
                        disposition = "INFO"
                issues.append(issue("DUPLICATE_STEP_COUNT", disposition, f"Step Count {step_count!r} occurs on two distinct source rows.", seen_step_counts[step_key], source_ref))
            else:
                seen_step_counts[step_key] = source_ref

        operations = split_operations(step_description)
        # Integrations are named by the input workbook, not by site-specific defaults.
        system_cell = values.get("external_system")
        systems = [name.strip() for name in str(system_cell or "").split(",") if name.strip()]
        if systems:
            issues.append(issue("EXTERNAL_EFFECT_REFERENCE", "NEED_DECISION", "The STP step references an external system effect. Keep the SAP Fiori boundary and require explicit authorization for the send or handoff.", source_ref))

        history = {
            "tester": values.get("tester"),
            "actual_test_date": values.get("actual_test_date"),
            "output": values.get("historical_output"),
            "status": values.get("historical_status"),
            "may_be_current_input": False,
        }
        contract_steps.append(
            {
                "step_key": {"sheet": row["sheet"], "row": row["row"], "step_count": step_count, "operation_no": None},
                "module_raw": values.get("module"),
                "module_normalized": "STP",
                "case_id": None if case_id is None else str(case_id).strip(),
                "case_id_source": row["fields"].get("case_id"),
                "source_refs": {field: record["source_ref"] for field, record in row["fields"].items()},
                "step_count": step_count,
                "description": values.get("test_description") or values.get("business_step_description"),
                "app": {"raw_name": application, "app_id": None, "route": None, "surface": "UNKNOWN"},
                "operations": operations,
                "external_systems": systems,
                "dependency_reference_raw": values.get("dependency_reference"),
                "expected_result_raw": expected_result,
                "expected_assertions": [],
                "compiled_plan": None,
                "expected_state": None,
                "evidence_plan": [],
                "depends_on": [],
                "retry_policy": {
                    "read_max_diagnostic_recoveries": 1,
                    "mutation_auto_retry": False,
                    "validation_mode": "FAILURE_ONLY",
                    "reuse_terminal_observation": True,
                },
                "idempotency_policy": {"required_for_mutation": any(op["effect_class"] != "READ" for op in operations)},
                "history": history,
            }
        )

    test_data = extract_test_data(workbook, cached)
    cached.close()
    workbook.close()

    dispositions = {item["disposition"] for item in issues}
    status = "BLOCKED" if "BLOCKED" in dispositions else "NEED_DECISION" if "NEED_DECISION" in dispositions else "SUCCESS"
    case_for_run = requested_case_id or (all_row_case_ids[0] if len(all_row_case_ids) == 1 else resolved.stem)
    safe_case = re.sub(r"[^0-9A-Za-z._-]+", "-", str(case_for_run)).strip("-") or "test-case"
    effective_run_id = run_id or f"{safe_case}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    return {
        "status": status,
        "contract_version": 1,
        "run_id": effective_run_id,
        "source": {
            "path": str(resolved),
            "sha256": sha256,
            "source_kind": "xlsx",
            "sheet": header_tables[0]["sheet"] if len(header_tables) == 1 else None,
            "header_row": header_tables[0]["header_row"] if len(header_tables) == 1 else None,
            "tables": header_tables,
            "snapshot_of": None,
            "snapshot_verified_by": None,
        },
        "identities": {
            "requested_case_id": requested_case_id,
            "filename_ids": filename_ids,
            "sheet_ids": unique_sheet_ids,
            "row_case_ids": all_row_case_ids,
            "row_id_sources": [row["fields"]["case_id"] for row in rows if "case_id" in row["fields"]],
        },
        "scope": {
            "module_required": "STP",
            "surface_required": "SAP_FIORI",
            "excluded_modules": sorted(excluded_modules),
            "selection": {
                "mode": scope_mode,
                "source_ref": scope_source,
                "selected_rows": [{"sheet": row["sheet"], "row": row["row"]} for row in selected_rows],
            },
        },
        "environment": {"host": None, "client": None, "classification": None, "user": None, "evidence_ref": None},
        "authorization": {"effect_classes": ["READ"], "objects": [], "granted_by": None, "granted_at": None, "expires_at": None},
        "test_data": test_data,
        "steps": contract_steps,
        "issues": issues,
        "metrics": {"stp_rows_found": len(rows), "selected_rows": len(selected_rows), "issues": len(issues)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--case-id")
    parser.add_argument("--scope-mode", choices=["row", "workbook"], default="row")
    parser.add_argument("--scope-source", help="Reference to the user's explicit scope selection; required for workbook scope.")
    parser.add_argument("--run-id")
    parser.add_argument("--snapshot", type=Path, help="Explicitly verified immutable snapshot to parse instead of the locked source.")
    parser.add_argument("--snapshot-verified-by", help="Evidence or decision reference that verified --snapshot for this source.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--strict", action="store_true", help="Return exit code 2 when the structured status is not SUCCESS.")
    args = parser.parse_args()

    if args.snapshot and not args.snapshot_verified_by:
        raise SystemExit("--snapshot requires --snapshot-verified-by; the script will not choose or trust a snapshot implicitly")
    parse_path = args.snapshot or args.source
    if args.scope_mode == "workbook" and not (args.case_id and (args.scope_source or "").strip()):
        parser.error("--scope-mode workbook requires --case-id and --scope-source")
    contract = build_contract(
        parse_path, args.case_id, args.run_id, scope_mode=args.scope_mode, scope_source=args.scope_source,
        source_identity_path=args.source,
    )
    if args.snapshot:
        contract["source"]["source_kind"] = "verified_snapshot"
        contract["source"]["snapshot_of"] = str(args.source.resolve())
        contract["source"]["snapshot_verified_by"] = args.snapshot_verified_by
    payload = json.dumps(contract, ensure_ascii=False, indent=2, default=json_value) + "\n"
    if args.output:
        if args.output.exists() and not args.force:
            raise SystemExit(f"Refusing to overwrite existing output: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.buffer.write(payload.encode("utf-8"))
    if args.strict and contract["status"] != "SUCCESS":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
