"""Minimal cached SAP RFC connector backed by PyRFC."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pyrfc import Connection


REQUIRED_SYSTEM_INFO_FIELDS = (
    "System ID",
    "Application Server",
    "System Number",
    "Client",
    "Language",
)


class SAPRFCConfigurationError(ValueError):
    """Raised when local SAP RFC configuration is incomplete or invalid."""


class SAPRFCConnectionUnavailable(RuntimeError):
    """Raised when a call is attempted before the cached RFC login exists."""


def _read_system_info(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SAPRFCConfigurationError(f"SAP System Info is missing: {path.name}")

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise SAPRFCConfigurationError(
                f"Invalid SAP System Info at line {line_number}; expected 'Field: value'."
            )
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()

    missing = [field for field in REQUIRED_SYSTEM_INFO_FIELDS if not values.get(field)]
    if missing:
        raise SAPRFCConfigurationError(
            f"Missing SAP System Info fields: {', '.join(missing)}"
        )
    if values.get("Connection Type", "RFC").upper() != "RFC":
        raise SAPRFCConfigurationError("Connection Type must be RFC.")
    return values


class SAPRFCConnector:
    """Own one cached PyRFC connection and expose the minimal call interface."""

    def __init__(self, system_info_path: Path, env_path: Path) -> None:
        self._system_info_path = system_info_path
        self._env_path = env_path
        self._system_info: dict[str, str] | None = None
        self._connection: Connection | None = None

    @property
    def is_connected(self) -> bool:
        return self._connection is not None

    @property
    def system_info(self) -> dict[str, str]:
        if self._system_info is None:
            self._system_info = _read_system_info(self._system_info_path)
        return dict(self._system_info)

    def connect(self) -> None:
        if self._connection is not None:
            return

        info = self.system_info
        load_dotenv(self._env_path, override=True)
        username = os.getenv("SAP_USERNAME", "").strip()
        password = os.getenv("SAP_PASSWORD", "")
        if not username or not password:
            raise SAPRFCConfigurationError(
                "SAP RFC credential is missing.\n"
                "Please configure SAP_USERNAME and SAP_PASSWORD in local .env."
            )

        self._connection = Connection(
            ashost=info["Application Server"],
            sysnr=info["System Number"],
            client=info["Client"],
            user=username,
            passwd=password,
            lang=info["Language"],
        )

    def call(self, function_name: str, **params: Any) -> dict[str, Any]:
        if self._connection is None:
            raise SAPRFCConnectionUnavailable(
                "RFC login is not established; call test_sap_rfc_connection first "
                "in the same MCP Server session."
            )
        return self._connection.call(function_name, **params)

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None
