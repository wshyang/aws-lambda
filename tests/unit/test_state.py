"""Delivery state: SSM writes, in-memory store and the dry-run guard (spec FR-3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sap_patch_day.models import KIND_BULLETIN, KIND_PENDING, StateSnapshot
from sap_patch_day.state import (
    PARAM_LAST_ATTEMPT_AT,
    PARAM_LAST_DELIVERED_KIND,
    PARAM_LAST_DELIVERED_MONTH,
    ReadOnlyStateStore,
    SsmStateStore,
    bulletin_delivered_for,
)
from tests.support.harness import delivered_state

MOMENT = datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
PREFIX = "/sap-security-patch-day/state/"


class RecordingSsmClient:
    """A real (tiny) implementation of the two SSM calls the store makes."""

    def __init__(self, parameters: dict[str, str] | None = None) -> None:
        self.parameters = parameters or {}
        self.puts: list[dict[str, Any]] = []

    def get_parameters_by_path(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["Path"] == PREFIX
        assert kwargs["Recursive"] is True
        return {
            "Parameters": [
                {"Name": f"{PREFIX}{name}", "Value": value}
                for name, value in self.parameters.items()
            ]
        }

    def put_parameter(self, **kwargs: Any) -> dict[str, Any]:
        self.puts.append(kwargs)
        name = kwargs["Name"][len(PREFIX) :]
        self.parameters[name] = kwargs["Value"]
        return {}


def test_read_maps_parameter_names_to_state_fields() -> None:
    client = RecordingSsmClient(
        {
            "last_delivered_month": "2026-09",
            "last_delivered_kind": KIND_BULLETIN,
            "last_delivered_at": "2026-09-08T08:05:00+00:00",
            "last_attempt_at": "2026-10-13T08:05:00+00:00",
            "last_watchdog_alert_month": "2026-08",
            "last_watchdog_alert_at": "2026-08-12T01:00:00+00:00",
        }
    )
    snapshot = SsmStateStore(client, PREFIX).read()
    assert snapshot.last_delivered_month == "2026-09"
    assert snapshot.last_delivered_kind == KIND_BULLETIN
    assert snapshot.last_watchdog_alert_month == "2026-08"


def test_read_defaults_to_unset_when_nothing_is_stored() -> None:
    snapshot = SsmStateStore(RecordingSsmClient(), PREFIX).read()
    assert snapshot == StateSnapshot()


def test_read_tolerates_a_prefix_without_a_trailing_slash() -> None:
    client = RecordingSsmClient({"last_delivered_month": "2026-09"})
    assert SsmStateStore(client, PREFIX.rstrip("/")).read().last_delivered_month == "2026-09"


def test_record_attempt_overwrites_one_parameter() -> None:
    client = RecordingSsmClient()
    SsmStateStore(client, PREFIX).record_attempt(MOMENT)
    assert client.puts == [
        {
            "Name": f"{PREFIX}{PARAM_LAST_ATTEMPT_AT}",
            "Value": "2026-10-13T08:05:00+00:00",
            "Type": "String",
            "Overwrite": True,
        }
    ]


def test_record_delivery_writes_month_kind_and_timestamp() -> None:
    client = RecordingSsmClient()
    SsmStateStore(client, PREFIX).record_delivery(month="2026-10", kind=KIND_BULLETIN, at=MOMENT)
    written = {put["Name"]: put["Value"] for put in client.puts}
    assert written[f"{PREFIX}{PARAM_LAST_DELIVERED_MONTH}"] == "2026-10"
    assert written[f"{PREFIX}{PARAM_LAST_DELIVERED_KIND}"] == KIND_BULLETIN
    assert all(put["Overwrite"] is True for put in client.puts)


def test_in_memory_store_round_trip() -> None:
    store = delivered_state("2026-10")
    assert bulletin_delivered_for("2026-10", store.read()) is True
    assert bulletin_delivered_for("2026-11", store.read()) is False
    store.record_attempt(MOMENT)
    assert store.read().last_attempt_at == "2026-10-13T08:05:00+00:00"


def test_bulletin_delivered_requires_the_bulletin_kind() -> None:
    """A pending notice must not count as a delivered bulletin (spec FR-3.6)."""
    store = delivered_state("2026-10")
    store.record_delivery(month="2026-10", kind=KIND_PENDING, at=MOMENT)
    assert bulletin_delivered_for("2026-10", store.read()) is False


def test_read_only_store_passes_reads_through_and_drops_writes() -> None:
    client = RecordingSsmClient({"last_delivered_month": "2026-09"})
    store = ReadOnlyStateStore(SsmStateStore(client, PREFIX))
    assert store.read().last_delivered_month == "2026-09"
    store.record_attempt(MOMENT)
    store.record_delivery(month="2026-10", kind=KIND_BULLETIN, at=MOMENT)
    store.record_watchdog_alert(month="2026-10", at=MOMENT)
    assert client.puts == []
    assert client.parameters == {"last_delivered_month": "2026-09"}
