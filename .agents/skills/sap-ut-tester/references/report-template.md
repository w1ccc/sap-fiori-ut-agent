# Excel execution report

Run the Python renderer from the project root with `openpyxl>=3.1` installed.

```sh
python .agents/skills/sap-ut-tester/scripts/render_test_report.py initialize conversation.json
python .agents/skills/sap-ut-tester/scripts/render_test_report.py update conversation.json --case-id CASE-1 --run-manifest run.json
```

The conversation manifest supplies `conversation_id`, `report_started_at`, `report_path`, and `case_entries`. Each entry has `case_id` and either a full `execution_contract` or a `contract_ref` path. Set `report_path` to an XLSX path inside the project’s `results` directory. Relative input paths resolve from the working directory. A minimal synthetic manifest looks like this:

```json
{
  "conversation_id": "example-session",
  "report_started_at": "2000-01-01T00:00:00Z",
  "report_path": "results/example.xlsx",
  "case_entries": [{"case_id": "CASE-1", "contract_ref": "contract.json"}]
}
```

Initialization creates an Overall sheet and one worksheet per Case. New entries append to the existing workbook on update. Supply one current, cumulative run per updated Case. Without an explicit run path, the renderer reads the entry’s `run_manifest`, `run_manifest_ref`, or `<Case ID>/run-manifest.json` beside the conversation manifest. The run carries the same conversation and Case IDs, the bound environment when present, an `execution_status`, and `steps`.

Each result joins to a source step by `step_key.sheet` and `step_key.row`. A legacy row number alone must identify exactly one source step. The output preserves source order and separates actual values from unresolved SAP errors. Set pending steps and the Case to `PENDING_EXTERNAL` while waiting for manual work. A successful workbook write returns `report_status=SUCCESS` independently of the Case’s business verdict.

A report is bound to its conversation, original start time, environment classification, and client. A failed update leaves the existing file available for a report-only retry from the same saved manifests. A session reconnect failure during a business resume keeps the original checkpoint and workbook.
