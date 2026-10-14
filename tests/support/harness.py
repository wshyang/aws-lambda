"""Harness that assembles the handler's ports for a test."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sap_patch_day.clock import FixedClock
from sap_patch_day.config import MappingConfigSource, load_config
from sap_patch_day.handler import RunContext
from sap_patch_day.httpclient import HttpClient, StaticHttpClient
from sap_patch_day.mailer import Mailer
from sap_patch_day.models import KIND_BULLETIN
from sap_patch_day.state import InMemoryStateStore, StateStore
from tests.support.fakes import RecordingMailer

DEFAULT_SENDER = "sap-patch-day@example.com"
DEFAULT_RECIPIENTS = "patch-day-team@example.test,security-ops@example.test"
DEFAULT_CONFIG_VALUES: Mapping[str, str] = {
    "sender": DEFAULT_SENDER,
    "recipients": DEFAULT_RECIPIENTS,
    "subject_prefix": "[SAP Security Patch Day]",
}


@dataclass
class Harness:
    """The assembled run, plus the ports a test wants to inspect."""

    run: RunContext
    mailer: Mailer
    state: StateStore
    http: HttpClient

    def recording_mailer(self) -> RecordingMailer:
        if not isinstance(self.mailer, RecordingMailer):
            raise AssertionError("harness was built with a different mailer")
        return self.mailer

    def in_memory_state(self) -> InMemoryStateStore:
        if not isinstance(self.state, InMemoryStateStore):
            raise AssertionError("harness was built with a different state store")
        return self.state


def build_harness(
    pages: Mapping[str, str],
    moment: datetime,
    *,
    config_values: Mapping[str, str] | None = None,
    environ: Mapping[str, str] | None = None,
    state: StateStore | None = None,
    mailer: Mailer | None = None,
    http: HttpClient | None = None,
) -> Harness:
    """Assemble a :class:`RunContext` over the given recorded pages and moment."""
    values = {**DEFAULT_CONFIG_VALUES, **(config_values or {})}
    config = load_config(MappingConfigSource(values).read(), environ or {})
    resolved_mailer = mailer if mailer is not None else RecordingMailer()
    resolved_state = state if state is not None else InMemoryStateStore()
    return Harness(
        run=RunContext(
            config=config,
            http=http if http is not None else StaticHttpClient(pages),
            mailer=resolved_mailer,
            state=resolved_state,
            clock=FixedClock(moment),
        ),
        mailer=resolved_mailer,
        state=resolved_state,
        http=http if http is not None else StaticHttpClient(pages),
    )


def delivered_state(month_label: str) -> InMemoryStateStore:
    """A store that already recorded a bulletin delivery for ``month_label``."""
    store = InMemoryStateStore()
    store.record_delivery(
        month=month_label, kind=KIND_BULLETIN, at=datetime(2026, 10, 13, 8, 5, tzinfo=UTC)
    )
    return store
