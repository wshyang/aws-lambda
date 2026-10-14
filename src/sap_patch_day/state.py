"""Delivery state, behind a port (spec FR-3, addendum 0006).

State is six SSM parameters, which is enough to make a monthly job idempotent
without inventing a database. Writes happen only after Amazon SES has accepted a
message, so a crash can lose an idempotency record but never a delivery.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from .models import KIND_BULLETIN, StateSnapshot, state_from_parameters

LOGGER = logging.getLogger("sap_patch_day.state")

PARAM_LAST_DELIVERED_MONTH = "last_delivered_month"
PARAM_LAST_DELIVERED_KIND = "last_delivered_kind"
PARAM_LAST_DELIVERED_AT = "last_delivered_at"
PARAM_LAST_ATTEMPT_AT = "last_attempt_at"
PARAM_LAST_WATCHDOG_ALERT_MONTH = "last_watchdog_alert_month"
PARAM_LAST_WATCHDOG_ALERT_AT = "last_watchdog_alert_at"

UNSET = "-"


class StateStore(Protocol):
    """Reads and writes the delivery state."""

    def read(self) -> StateSnapshot: ...

    def record_attempt(self, at: datetime) -> None: ...

    def record_delivery(self, month: str, kind: str, at: datetime) -> None: ...

    def record_watchdog_alert(self, month: str, at: datetime) -> None: ...


class SsmStateStore:
    """State held in SSM Parameter Store under the state path."""

    def __init__(self, client: Any, path_prefix: str) -> None:
        self._client = client
        self._prefix = path_prefix if path_prefix.endswith("/") else f"{path_prefix}/"

    def read(self) -> StateSnapshot:
        response = self._client.get_parameters_by_path(
            Path=self._prefix, Recursive=True, WithDecryption=False
        )
        values = {
            _relative_name(parameter.get("Name", ""), self._prefix): parameter.get("Value", "")
            for parameter in response.get("Parameters", [])
        }
        return state_from_parameters(values)

    def record_attempt(self, at: datetime) -> None:
        self._write({PARAM_LAST_ATTEMPT_AT: _stamp(at)})

    def record_delivery(self, month: str, kind: str, at: datetime) -> None:
        self._write(
            {
                PARAM_LAST_DELIVERED_MONTH: month,
                PARAM_LAST_DELIVERED_KIND: kind,
                PARAM_LAST_DELIVERED_AT: _stamp(at),
            }
        )

    def record_watchdog_alert(self, month: str, at: datetime) -> None:
        self._write(
            {
                PARAM_LAST_WATCHDOG_ALERT_MONTH: month,
                PARAM_LAST_WATCHDOG_ALERT_AT: _stamp(at),
            }
        )

    def _write(self, values: Mapping[str, str]) -> None:
        for name, value in values.items():
            self._client.put_parameter(
                Name=f"{self._prefix}{name}", Value=value, Type="String", Overwrite=True
            )


class InMemoryStateStore:
    """State held in this process: the dry run, and the unit tests."""

    def __init__(self, initial: StateSnapshot | None = None) -> None:
        self._snapshot = initial or StateSnapshot()

    @property
    def snapshot(self) -> StateSnapshot:
        return self._snapshot

    def read(self) -> StateSnapshot:
        return self._snapshot

    def record_attempt(self, at: datetime) -> None:
        self._snapshot = self._snapshot.with_attempt(at)

    def record_delivery(self, month: str, kind: str, at: datetime) -> None:
        self._snapshot = replace(
            self._snapshot,
            last_delivered_month=month,
            last_delivered_kind=kind,
            last_delivered_at=_stamp(at),
        )

    def record_watchdog_alert(self, month: str, at: datetime) -> None:
        self._snapshot = replace(
            self._snapshot,
            last_watchdog_alert_month=month,
            last_watchdog_alert_at=_stamp(at),
        )


class ReadOnlyStateStore:
    """Wraps a store and refuses to write: used when ``DRY_RUN`` is on.

    Reads pass through, so a dry run still reports "already delivered" when the
    month really has been delivered (spec FR-3.9).
    """

    def __init__(self, delegate: StateStore, logger: logging.Logger = LOGGER) -> None:
        self._delegate = delegate
        self._logger = logger

    def read(self) -> StateSnapshot:
        return self._delegate.read()

    def record_attempt(self, at: datetime) -> None:
        self._logger.info("dry run: not writing attempt timestamp %s", _stamp(at))

    def record_delivery(self, month: str, kind: str, at: datetime) -> None:
        self._logger.info("dry run: not recording %s delivery for %s", kind, month)

    def record_watchdog_alert(self, month: str, at: datetime) -> None:
        self._logger.info("dry run: not recording watchdog alert for %s", month)


def bulletin_delivered_for(target_label: str, snapshot: StateSnapshot) -> bool:
    """Whether the bulletin for ``target_label`` has already been emailed."""
    month, kind = snapshot.delivered_bulletins
    return month == target_label and kind == KIND_BULLETIN


def _relative_name(parameter_name: str, prefix: str) -> str:
    return parameter_name[len(prefix) :] if parameter_name.startswith(prefix) else parameter_name


def _stamp(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()
