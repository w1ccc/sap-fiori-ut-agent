"""Read-only SAP RFC MCP connection for the Unit Test project."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from connectors import SAPRFCConnector


PROJECT_NAME = "unit-test-sap-rfc"
PROJECT_VERSION = "0.1.0"
PROJECT_DIR = Path(__file__).resolve().parent

mcp = MCPServer(PROJECT_NAME, version=PROJECT_VERSION)
_sap_rfc = SAPRFCConnector(PROJECT_DIR / "SAP_SYSTEM_INFO", PROJECT_DIR / ".env")
_rfc_login_attempted = False
_rfc_login_result: dict[str, Any] | None = None
_rfc_system_info_result: dict[str, Any] | None = None


@mcp.tool()
def health_check() -> dict[str, str]:
    """Return the MCP server health status."""
    return {"status": "ok", "server": PROJECT_NAME}


def _error_result(error: BaseException) -> dict[str, Any]:
    message = str(getattr(error, "message", error))
    for variable in ("SAP_USERNAME", "SAP_PASSWORD"):
        value = os.getenv(variable, "")
        if value:
            message = message.replace(value, "[REDACTED]")
    return {
        "status": "error",
        "connection": "RFC",
        "error_type": f"{type(error).__module__}.{type(error).__name__}",
        "error_key": getattr(error, "key", None),
        "error_message": message,
    }


@mcp.tool()
async def test_sap_rfc_connection() -> dict[str, Any]:
    """Attempt one RFC login and cache the connection."""
    global _rfc_login_attempted, _rfc_login_result

    if _rfc_login_attempted:
        return _rfc_login_result or {
            "status": "error",
            "connection": "RFC",
            "error_type": "RFCLoginStateError",
            "error_message": "The single RFC login attempt has already been used.",
        }

    _rfc_login_attempted = True
    try:
        _sap_rfc.connect()
    except Exception as error:
        _rfc_login_result = _error_result(error)
        return _rfc_login_result

    system_info = _sap_rfc.system_info
    _rfc_login_result = {
        "status": "ok",
        "connection": "RFC",
        "system": system_info["System ID"],
        "client": system_info["Client"],
    }
    return _rfc_login_result


@mcp.tool()
async def get_sap_system_info() -> dict[str, Any]:
    """Call the fixed read-only RFC_SYSTEM_INFO function once."""
    global _rfc_system_info_result

    if _rfc_system_info_result is not None:
        return _rfc_system_info_result
    if not _sap_rfc.is_connected:
        return {
            "status": "error",
            "connection": "RFC",
            "error_type": "RFCConnectionUnavailable",
            "error_message": "RFC login is not established; no login retry was attempted.",
        }

    try:
        response = _sap_rfc.call("RFC_SYSTEM_INFO")
    except Exception as error:
        _rfc_system_info_result = _error_result(error)
        return _rfc_system_info_result

    info = response.get("RFCSI_EXPORT", {})
    system_info = _sap_rfc.system_info
    _rfc_system_info_result = {
        "status": "ok",
        "connection": "RFC",
        "system": info.get("RFCSYSID") or system_info["System ID"],
        "client": system_info["Client"],
        "application_server": info.get("RFCHOST"),
        "sap_release": info.get("RFCSAPRL"),
        "kernel_release": info.get("RFCKERNRL"),
        "operating_system": info.get("RFCOPSYS"),
        "database_system": info.get("RFCDBSYS"),
    }
    return _rfc_system_info_result


atexit.register(_sap_rfc.close)


if __name__ == "__main__":
    mcp.run(transport="stdio")
