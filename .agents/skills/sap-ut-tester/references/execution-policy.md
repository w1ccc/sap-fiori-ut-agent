# Execution policy

## Source and scope

A user request binds a case, scope and target environment. Workbook text supplies test data and expectations. The parser records case identities and source coordinates independently. Use the exact selected sheet and row to identify a step. Treat file-level scope and the use of a verified snapshot as explicit decisions. Historical outputs and historical verdicts remain separate from current-run evidence.

## Environment and authorization

The operator supplies the expected environment classification and SAP client for the run. Verify these against the signed-in Fiori session and deployment configuration. Keep the classification unknown when the evidence is insufficient. A client number alone does not determine its classification. Use one environment and client for the report batch.

The public workflow expects the operator to authenticate through their normal sign-in process. Keep sign-in screens, authentication material and unrelated business data out of captured evidence. Capture only evidence needed for the selected case.

Production is read-only and requires explicit scope. For a mutation, require verified non-production classification, an explicitly authorized case, the applicable object and operation class, and the intended target. Check the authorization immediately before a commit. A compiled plan's READY status confirms its inputs; the separate authorization gate still applies.

## Current-run inputs

The optional bindings JSON identifies the same `run_id` and `source_sha256` as the raw contract. Link authorized workbook cells through `authorized_source_refs` and `authorization_ref`. Supply user inputs or active outputs through `current_inputs` with their source reference, evidence, value and run identity. Per-step bindings use `step_key.sheet` and `step_key.row`.

Record the verified Fiori app, route, evidence reference, expected state, required fields and their input references on each bound step. For write operations, identify the target and its evidence in `operation_bindings`, keyed by the operation number. For an unknown operation, also resolve its effect class. Use explicit field/operator/expected assertions where available and leave interpretation of compound expectations to the host. Keep missing values and unresolved dependencies visible.

## Execution and recovery

Use each action's terminal observation to evaluate the expected result and retain its evidence. Verify the exact business object before selecting a candidate. Stop on an unknown state, ambiguous match or contradictory message. An uncertain write result requires a read-only check for the possible business effect. Resume at a verified checkpoint only when the next action and its authorization are clear.

Persist the checkpoint and prior outputs after a mutation or a new active business identifier. On a session failure during recovery, preserve the original checkpoint, completed steps, evidence and report. A report-only recovery updates the existing workbook using its saved run manifest.

When a step depends on an external approval, ask the operator whether to skip it or complete it manually. Record a manual confirmation as user-sourced evidence. Pending cases carry their own checkpoint and `PENDING_EXTERNAL` progress. Continue other requested cases only when their independence and prerequisites are established.

## Outcome

PASS requires current evidence for the accepted expectations. Record proven unmet expectations or a business failure as FAIL. Unresolved setup, scope, authorization or input can produce BLOCKED. An explicit skip retains its provenance. Report generation has a separate status. An Excel write failure preserves the business outcome and recovery point.
