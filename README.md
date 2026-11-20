# SAP Fiori UT Agent

A project-local agent skill for testing SAP Fiori source-to-pay scenarios. It selects workbook rows whose Module is STP, compiles traceable step inputs, preserves checkpoints across interrupted sessions, and writes an Excel report from the recorded results.

The parser, input compiler, scheduler, recovery handler and report writer run locally. The included workflow descriptions guide a compatible agent with an authenticated Fiori browser session. The optional RFC server exposes connection diagnostics and system metadata. The offline replay uses generated examples and requires no SAP connection.

## Run the offline replay

Use Python 3.10 or later. From the repository root, run

```sh
python -m pip install -r requirements.txt
python -B .agents/skills/sap-ut-tester/scripts/verify_execution_replay.py
```

To inspect a contract from a synthetic workbook, run

```sh
python .agents/skills/sap-ut-tester/tests/make_demo_workbook.py demo.xlsx
python .agents/skills/sap-ut-tester/scripts/build_test_contract.py demo.xlsx --case-id CASE_001 --output contract.json
python .agents/skills/sap-ut-tester/scripts/compile_execution_input.py contract.json --output compiled.json
```

A step can require a verified app binding, field source, or approval before execution. See the [skill instructions](.agents/skills/sap-ut-tester/SKILL.md), [execution policy](.agents/skills/sap-ut-tester/references/execution-policy.md), and [report format](.agents/skills/sap-ut-tester/references/report-template.md).

## Optional read-only RFC MCP server

To use the three tools in `server.py`, install the SAP NetWeaver RFC SDK and a compatible SAP PyRFC distribution for your platform, then install `requirements-rfc.txt` in the same Python environment. Create local copies of `SAP_SYSTEM_INFO.example` and `.env.example` named `SAP_SYSTEM_INFO` and `.env`. Supply your own connection settings and credentials there. Run `python server.py` as a stdio MCP process.

`health_check` checks the server process. `test_sap_rfc_connection` attempts one RFC login per process. `get_sap_system_info` reads system metadata from that connection. Each live run needs its own authorized environment and session. Keep workbooks, screenshots, credentials and generated reports local.

## License

[MIT](LICENSE). SAP software and SDKs are obtained separately.
