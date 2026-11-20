---
name: sap-ut-tester
description: Plan and run evidence-based SAP Fiori tests from spreadsheet cases scoped to the STP module. Use for an explicitly selected test case, a case batch, or a checkpoint resume.
---

# SAP Fiori test execution

Select the requested workbook and case. Scope comes from the source Module cell, trimmed and compared case-insensitively with `STP`. Preserve source order, cell references and the original expected result.

1. Run `python scripts/build_test_contract.py SOURCE.xlsx --case-id CASE_ID --output raw.json`. Use `--scope-mode workbook --scope-source DECISION_REF` only for an explicit whole-workbook scope. Record the resulting source hash and raw issues.
2. Run `python scripts/compile_execution_input.py raw.json --output compiled.json`. Add `--bindings CURRENT_RUN.json` for verified input sources and app targets. Follow [the execution policy](references/execution-policy.md) for unresolved scope, input or authorization. Reuse compiled results across the case.
3. Follow [the batch workflow](workflows/execute-fiori-ut-batch.json) for report ownership, case selection and checkpoint recovery. It delegates selected cases to [the case workflow](workflows/execute-fiori-ut.json). A fully specified, single-object read can use [the read-only workflow](workflows/verify-existing-object.json).
4. Execute browser operations through an operator-provided Fiori browser integration using the [action catalog](actions/action-catalog.json). Use a verified signed-in session, and compare its environment and client with the requested target before any write. The JSON workflows describe the host's responsibilities. Python commands provide parsing, compilation, scheduling, recovery and reporting.
5. Record the observed result, source of evidence and step status. Update the batch's existing Excel report for progress and final outcomes according to [the report format](references/report-template.md).

Script paths above are relative to this skill directory. Use absolute paths for input files and the report manifest when working from a different directory. Run `python scripts/verify_execution_replay.py` for the synthetic offline regression.
