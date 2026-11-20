"""Compile one parsed Test Contract and current-run bindings into per-step Action inputs."""

import argparse
import copy
import json
import re
import sys
from pathlib import Path

from build_test_contract import VERB_EFFECTS, classify_operation, issue, normalize_case_id, normalize_module


def row_key(step):
    key = step["step_key"]
    return key["sheet"], key["row"]


def indexed(items, key):
    result = {}
    for item in items:
        value = key(item)
        if value in result:
            raise ValueError(f"Duplicate binding key: {value}")
        result[value] = item
    return result


def default_assertions(expected, source_ref):
    if expected is None or not str(expected).strip():
        return []
    literal = re.fullmatch(r"([\w /().-]+?)\s*=\s*([^=\n]+)", str(expected).strip())
    if literal:
        return [{"operator": "exact", "field": literal[1].strip(), "expected": literal[2].strip(),
                 "source_ref": source_ref}]
    return [{"operator": "semantic_review", "expected": expected, "source_ref": source_ref}]


def compile_step(step, binding, catalog):
    sheet, row = row_key(step)
    ref = f"{sheet}!{row}"
    required = []
    expected = step.get("expected_result_raw")
    refs = step.get("source_refs", {})
    assertions = default_assertions(expected, refs.get("expected_result", ref))
    if not assertions:
        required.append("MISSING_EXPECTED_RESULT")
    if assertions and binding.get("assertions") and binding.get("evidence_ref"):
        checks = binding["assertions"]
        for check in checks:
            if (not check.get("field") or check.get("operator") not in {"exact", "count", "state"}
                    or check.get("expected") is None):
                raise ValueError(f"{ref}: incomplete field/operator/expected assertion")
        if assertions[0]["operator"] == "exact":
            literal = assertions[0]
            if any(any(check[key] != literal[key] for key in ("field", "operator", "expected")) for check in checks):
                raise ValueError(f"{ref}: binding contradicts the source's exact assertion")
        else:
            assertions = [{**check, "source_ref": refs.get("expected_result", ref),
                           "expected_result_raw": expected,
                           "interpretation_evidence_ref": check.get("evidence_ref") or binding["evidence_ref"]}
                          for check in checks]
    # Archived contracts used READ for unrecognized verbs.
    operations = []
    for operation in step["operations"]:
        effect, triggers = classify_operation(operation["raw_text"])
        verified = binding.get("operation_bindings", {}).get(str(operation["operation_no"]), {})
        has_target = bool(verified.get("target") and verified.get("evidence_ref"))
        if effect == "UNKNOWN" and has_target:
            if verified.get("effect_class") in {*VERB_EFFECTS.values(), "READ"}:
                effect = verified["effect_class"]
        if effect not in {"UNKNOWN", "READ"} and not has_target:
            required.append(f"NEED_OPERATION_TARGET:{operation['operation_no']}")
        operations.append({"operation_no": operation["operation_no"], "raw_text": operation["raw_text"],
                           "effect_class": effect, "effect_triggers": triggers, "target": verified.get("target"),
                           "target_evidence_ref": verified.get("evidence_ref")})
    if not operations or any(item["effect_class"] == "UNKNOWN" for item in operations):
        required.append("UNRESOLVED_OPERATION")

    required_fields = binding.get("required_fields")
    if required_fields is not None and not (isinstance(required_fields, list)
            and all(isinstance(field, str) and field.strip() for field in required_fields)):
        raise ValueError(f"{ref}: required_fields must be a list of field names")
    profile = binding.get("app", {})
    if (not binding.get("evidence_ref") or profile.get("surface") != "SAP_FIORI"
            or not profile.get("app_id") or not profile.get("route")
            or not binding.get("expected_state") or not isinstance(required_fields, list)):
        required.append("NEED_VERIFIED_PLAN")
    if not isinstance(binding.get("inputs", {}), dict):
        raise ValueError(f"{ref}: inputs must map field names to source references")
    fields = list(required_fields or [])
    sources = dict(binding.get("inputs", {}))
    for field in fields:
        matches = [ref for ref, item in catalog.items() if item.get("source_kind") == "source_test_data"
                   and str(item.get("name", "")).casefold() == field.casefold()]
        if field not in sources and len(matches) == 1:
            sources[field] = matches[0]
    if not fields and any(op["effect_class"] == "DRAFT" or {"fill", "input", "assign", "upload"}.intersection(op["effect_triggers"])
                          for op in operations):
        required.append("NEED_REQUIRED_FIELDS")
    values = {}
    for field in dict.fromkeys([*fields, *sources]):
        source = sources.get(field)
        item = catalog.get(source)
        if not item or item.get("authorized_for_run") is not True or item.get("canonical_value") in (None, ""):
            required.append(f"NEED_SOURCED_INPUT:{field}")
            continue
        values[field] = {"value": item["canonical_value"], "source_ref": source,
                         "source_kind": item["source_kind"]}

    dependencies = step.get("depends_on", [])
    if binding.get("depends_on"):
        bound_dependencies = binding["depends_on"]
        if not isinstance(bound_dependencies, list) or not all(isinstance(d, (str, dict)) and d for d in bound_dependencies):
            raise ValueError(f"{ref}: depends_on must list resolved dependencies")
        dependencies = [*dependencies, *[d for d in bound_dependencies if d not in dependencies]]
    raw_dependency = step.get("dependency_reference_raw")
    if raw_dependency not in (None, "") and not dependencies:
        required.append("UNRESOLVED_DEPENDENCY")
    external = set(step.get("external_systems", []))
    if not external and "EXTERNAL_EFFECT_REFERENCE" in step.get("step_issues", []):
        external.add("EXTERNAL_HANDOFF")
    plan = {"step_key": step["step_key"], "app": profile, "source_app": step["app"]["raw_name"],
            "operations": operations, "inputs": values, "expected_assertions": assertions,
            "depends_on": dependencies, "external_systems": sorted(external),
            "expected_state": binding.get("expected_state"), "plan_evidence_ref": binding.get("evidence_ref"),
            "evidence_plan": ["terminal_observation"], "missing": required}
    plan["status"] = "NEED_DECISION" if required else "READY"
    return plan


def compile_input(raw, bindings=None):
    bindings = bindings or {}
    sha = raw["source"]["sha256"]
    if bindings and (not sha or bindings.get("source_sha256") != sha or bindings.get("run_id") != raw["run_id"]):
        raise ValueError("Bindings must belong to this source hash and run ID")
    contract = copy.deepcopy(raw)
    steps = indexed(contract["steps"], row_key)
    supplied = indexed(bindings.get("steps", []), row_key)
    if supplied.keys() - steps.keys():
        raise ValueError("Binding references a row outside the selected scope")
    catalog = indexed((item for item in raw.get("test_data", []) if item.get("source_kind") == "source_test_data"),
                      lambda item: item["source_ref"])
    for source in bindings.get("authorized_source_refs", []):
        if source not in catalog or catalog[source].get("source_kind") != "source_test_data":
            raise ValueError(f"Unknown workbook input: {source}")
        if not bindings.get("authorization_ref"):
            raise ValueError("Authorized workbook inputs require the current authorization reference")
        catalog[source] = {**catalog[source], "authorized_for_run": True}
    for item in bindings.get("current_inputs", []):
        source = item["source_ref"]
        kind = item["source_kind"]
        if (not source or source in catalog or item.get("run_id") != raw["run_id"]
                or kind not in {"current_run_output", "user_supplied"} or not item.get("evidence_ref")
                or (kind == "current_run_output" and item.get("active") is not True)):
            raise ValueError(f"Invalid current-run input: {source}")
        catalog[source] = item

    case_issues = []
    resolutions = []
    row_refs = {f"{sheet}!{row}": step for (sheet, row), step in steps.items()}
    ids = raw["identities"]
    requested = normalize_case_id(ids.get("requested_case_id"))
    row_ids = {normalize_case_id(i) for i in ids["row_case_ids"]}
    file_ids = {normalize_case_id(i) for i in ids["filename_ids"] + ids["sheet_ids"]}
    selection = raw["scope"].get("selection", {})
    unresolved = [item for item in raw["issues"] if item["disposition"] != "INFO"]
    for item in unresolved:
        code, refs = item["code"], item.get("source_refs", [])
        if code in {"EXTERNAL_EFFECT_REFERENCE", "MISSING_EXPECTED_RESULT", "MISSING_FIORI_APPLICATION"} and refs and all(ref in row_refs for ref in refs):
            for ref in refs:
                row_refs[ref].setdefault("step_issues", []).append(code)
        elif (code == "CASE_IDENTITY_CONFLICT" and sha and steps and requested in row_ids
              and all(normalize_case_id(s["case_id"]) == requested for s in steps.values())
              and ((selection.get("mode") == "row" and selection.get("source_ref"))
                   or (len(unresolved) == 1 and file_ids <= row_ids))):
            decision = "EXPLICIT_ROW_SCOPE" if selection.get("source_ref") else "RESOLVED_EXPECTED_MULTI_CASE"
            resolutions.append({"issue": code, "decision": decision,
                                "source_refs": [*refs, *([selection["source_ref"]] if selection.get("source_ref") else [])]})
        else:
            case_issues.append(item)
    frozen_rows = [{"sheet": sheet, "row": row} for sheet, row in steps]
    invalid_scope = (any(normalize_module(s["module_raw"]) != "STP" for s in steps.values())
                     or (selection and selection.get("selected_rows") != frozen_rows))
    if invalid_scope or (not case_issues and (not steps or not sha)):
        case_issues.append(issue("UNVERIFIED_SCOPE", "BLOCKED", "No verified source and exact STP row selection.",
                                 raw["source"]["path"]))
    if not unresolved and raw["status"] != "SUCCESS":
        case_issues.append(issue("UNRESOLVED_CONTRACT", raw["status"], "The raw contract has an unresolved status.",
                                 raw["source"]["path"]))
    for key, step in steps.items():
        plan = compile_step(step, supplied.get(key, {}), catalog)
        if "MISSING_FIORI_APPLICATION" in step.get("step_issues", []):
            plan["missing"].append("MISSING_FIORI_APPLICATION")
            plan["status"] = "NEED_DECISION"
        step.update(compiled_plan=plan, operations=plan["operations"], expected_assertions=plan["expected_assertions"],
                    expected_state=plan["expected_state"], depends_on=plan["depends_on"], evidence_plan=plan["evidence_plan"])
        step["idempotency_policy"] = {**step.get("idempotency_policy", {}),
                                      "required_for_mutation": any(op["effect_class"] != "READ" for op in plan["operations"])}
    # The specialized workflow verifies a single exact object from one ready, independent step.
    contract["readonly_plan"] = None
    if len(steps) == 1 and not case_issues:
        key, step = next(iter(steps.items()))
        plan = step["compiled_plan"]
        lookup = supplied.get(key, {}).get("readonly_search", {})
        search = plan["inputs"].get(lookup.get("input_field"))
        if (plan["status"] == "READY" and all(op["effect_class"] == "READ" for op in plan["operations"])
                and not plan["depends_on"] and not plan["external_systems"] and search
                and all(lookup.get(k) for k in ("search_control", "readonly_target", "record_identity_fields"))
                and all(a["operator"] in {"exact", "state", "count"} for a in plan["expected_assertions"])):
            contract["readonly_plan"] = {**plan, **{k: lookup[k] for k in ("search_control", "readonly_target", "record_identity_fields")},
                                         "search_value": search["value"], "search_source_ref": search["source_ref"]}
    effective = "BLOCKED" if any(i["disposition"] == "BLOCKED" for i in case_issues) else "NEED_DECISION" if case_issues else "SUCCESS"
    contract["status"] = effective
    contract["issues"] = case_issues
    return {"parser_status": raw["status"], "effective_status": effective, "effective_issues": case_issues,
            "resolutions": resolutions, "execution_contract": contract}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--bindings", type=Path, help="Verified plans and sourced inputs for this run")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        raw = json.loads(args.contract.read_text(encoding="utf-8-sig"))
        bindings = json.loads(args.bindings.read_text(encoding="utf-8-sig")) if args.bindings else None
        result = compile_input(raw, bindings)
    except (ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Invalid execution input: {error}\n")
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.resolve() in {p.resolve() for p in (args.contract, args.bindings) if p}:
            parser.error("Output must be separate from the raw contract and bindings")
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.buffer.write(payload.encode("utf-8"))


if __name__ == "__main__":
    main()
