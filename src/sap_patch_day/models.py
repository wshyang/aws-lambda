"""Canonical domain models.

One frozen dataclass per concept, in snake_case, is the single Python shape of
that concept everywhere in the code base. Conversion happens only at adapter
boundaries (spec §4). Nothing here mutates in place: transforms use
``dataclasses.replace`` or return new instances.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, time

MONTH_NAMES: tuple[str, ...] = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

SOURCE_SAP = "sap"
SOURCE_GITHUB = "github"

KIND_BULLETIN = "bulletin"
KIND_PENDING = "pending"
KIND_WATCHDOG = "watchdog"

NOTICE_PENDING = "pending"
NOTICE_UNVERIFIED = "unverified"
NOTICE_WATCHDOG = "watchdog"


def _validate_month(year: int, month: int) -> None:
    if not 1 <= month <= 12:
        raise ValueError(f"month must be between 1 and 12, got {month}")
    if year < 2000:
        raise ValueError(f"year must be 2000 or later, got {year}")


@dataclass(frozen=True)
class PatchDayMonth:
    """The patch-day month a run is about (SAP's monthly cycle)."""

    year: int
    month: int

    def __post_init__(self) -> None:
        _validate_month(self.year, self.month)

    @property
    def label(self) -> str:
        """Machine label, e.g. ``2026-10``."""
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def month_name(self) -> str:
        """English month name, e.g. ``October``."""
        return MONTH_NAMES[self.month - 1]

    @property
    def display(self) -> str:
        """Human label, e.g. ``October 2026``."""
        return f"{self.month_name} {self.year}"

    @property
    def bulletin_slug(self) -> str:
        """URL slug segment, e.g. ``october-2026``."""
        return f"{self.month_name.lower()}-{self.year}"

    @classmethod
    def parse(cls, text: str) -> PatchDayMonth:
        """Parse ``YYYY-MM``; raises ValueError on anything else."""
        parts = text.strip().split("-")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError(f"not a YYYY-MM month: {text!r}")
        return cls(year=int(parts[0]), month=int(parts[1]))


@dataclass(frozen=True)
class IndexEntry:
    """One patch-day date on the Security Notes & News index page."""

    month_name: str
    day: int
    url: str | None = None

    @property
    def is_linked(self) -> bool:
        """Whether the date has become a hyperlink on the index page."""
        return bool(self.url)


@dataclass(frozen=True)
class SecurityNote:
    """One row of the monthly notes table."""

    note_id: str
    note_url: str
    source: str
    title_html: str
    headline: str
    priority: str
    cvss_score: str
    cve_ids: tuple[str, ...] = ()
    product: str = ""
    versions: str = ""
    cvss_vector: str = ""
    cvss_url: str = ""
    is_update: bool = False
    update_period: str = ""

    @property
    def display_id(self) -> str:
        """Identifier as printed in the email (SAP note number or GHSA id)."""
        return self.note_id


@dataclass(frozen=True)
class Bulletin:
    """A retrieved monthly bulletin page."""

    target: PatchDayMonth
    source_url: str
    page_title: str
    intro_text: str
    notes: tuple[SecurityNote, ...]
    fetched_at: datetime
    link_transitioned: bool | None = None


@dataclass(frozen=True)
class BulletinSummary:
    """Counts derived from the parsed rows (spec FR-4.7)."""

    total: int
    critical: int
    high: int
    medium: int
    low: int
    other: int
    updates: int
    new_notes: int
    sap_notes: int
    github_advisories: int

    @classmethod
    def from_notes(cls, notes: tuple[SecurityNote, ...]) -> BulletinSummary:
        priorities = [note.priority.strip().lower() for note in notes]
        sources = [note.source for note in notes]
        updates = [note for note in notes if note.is_update]
        return cls(
            total=len(notes),
            critical=priorities.count("critical"),
            high=priorities.count("high"),
            medium=priorities.count("medium"),
            low=priorities.count("low"),
            other=len([p for p in priorities if p not in {"critical", "high", "medium", "low"}]),
            updates=len(updates),
            new_notes=len(notes) - len(updates),
            sap_notes=sources.count(SOURCE_SAP),
            github_advisories=sources.count(SOURCE_GITHUB),
        )


@dataclass(frozen=True)
class PublicationCheck:
    """Outcome of probing the canonical bulletin URL (spec FR-2)."""

    target: PatchDayMonth
    canonical_url: str
    outcome: str
    http_status: int | None = None
    link_transitioned: bool | None = None
    detail: str = ""
    bulletin: Bulletin | None = None

    @property
    def is_published(self) -> bool:
        return self.outcome == OUTCOME_PUBLISHED

    @property
    def is_unverified(self) -> bool:
        return self.outcome in {OUTCOME_UNVERIFIED, OUTCOME_UNVERIFIED_TABLE}


OUTCOME_PUBLISHED = "published"
OUTCOME_NOT_PUBLISHED = "not_published"
OUTCOME_UNVERIFIED = "unverified"
OUTCOME_UNVERIFIED_TABLE = "unverified_table"


@dataclass(frozen=True)
class StateSnapshot:
    """Delivery state read from SSM Parameter Store (spec FR-3)."""

    last_delivered_month: str = "-"
    last_delivered_kind: str = "-"
    last_delivered_at: str = "-"
    last_attempt_at: str = "-"
    last_watchdog_alert_month: str = "-"
    last_watchdog_alert_at: str = "-"

    def with_attempt(self, at: datetime) -> StateSnapshot:
        return replace(self, last_attempt_at=_isoformat(at))

    @property
    def delivered_bulletins(self) -> tuple[str, str]:
        """``(month, kind)`` of the last delivery, for readability at call sites."""
        return (self.last_delivered_month, self.last_delivered_kind)


@dataclass(frozen=True)
class Notice:
    """A status message that is not the bulletin itself (spec FR-5.8).

    ``kind`` is one of :data:`NOTICE_PENDING`, :data:`NOTICE_UNVERIFIED` or
    :data:`NOTICE_WATCHDOG`; ``detail`` is free text for the operator and never
    carries internal identifiers (spec FR-5.9).
    """

    kind: str
    target: PatchDayMonth
    observed_at: datetime
    detail: str = ""


@dataclass(frozen=True)
class Attachment:
    """A file attached to the outgoing message."""

    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class EmailContent:
    """The message body, rendered once and sent once."""

    subject: str
    html_body: str
    text_body: str
    attachment: Attachment | None = None


@dataclass(frozen=True)
class SendRequest:
    """Everything Amazon SES needs, as one parameter object."""

    recipients: tuple[str, ...]
    sender: str
    reply_to: str
    content: EmailContent


@dataclass(frozen=True)
class HttpPolicy:
    """Transport policy (spec §9.1).

    ``deadline_seconds`` is the budget for one invocation's HTTP work across
    both URLs: worst case is ``deadline_seconds + timeout_seconds``, which stays
    below the function's own timeout (NFR-4) instead of the run being killed
    mid-flight.
    """

    timeout_seconds: int = 20
    max_attempts: int = 3
    backoff_seconds: float = 2.0
    deadline_seconds: int = 30
    user_agent: str = "sap-patch-day-notifier/1 (aws-lambda; +https://support.sap.com)"

    def __post_init__(self) -> None:
        for name in ("timeout_seconds", "max_attempts", "deadline_seconds"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer, got {getattr(self, name)}")
        if self.backoff_seconds < 0:
            raise ValueError(f"backoff_seconds must not be negative, got {self.backoff_seconds}")


@dataclass(frozen=True)
class RuntimeConfig:
    """Validated runtime configuration: people from SSM, policy from the environment."""

    recipients: tuple[str, ...]
    sender: str
    reply_to: str = ""
    subject_prefix: str = "[SAP Security Patch Day]"
    attach_csv: bool = False
    notify_when_not_published: bool = True
    retry_window_end: time = time(20, 0)
    http: HttpPolicy = field(default_factory=HttpPolicy)
    configuration_set: str = ""
    dry_run: bool = False
    log_level: str = "INFO"
    config_path: str = "/sap-security-patch-day/"
    state_path: str = "/sap-security-patch-day/state/"

    @property
    def masked_recipients(self) -> tuple[str, ...]:
        """Recipients safe to write into a log line (spec FR-6.4)."""
        return tuple(mask_address(address) for address in self.recipients)


def mask_address(address: str) -> str:
    """Mask an address for logs: ``team@example.com`` -> ``t***@example.com``."""
    local, _, domain = address.partition("@")
    if not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def state_from_parameters(values: Mapping[str, str]) -> StateSnapshot:
    """Build a snapshot from SSM parameter names to values."""
    return StateSnapshot(
        last_delivered_month=values.get("last_delivered_month", "-"),
        last_delivered_kind=values.get("last_delivered_kind", "-"),
        last_delivered_at=values.get("last_delivered_at", "-"),
        last_attempt_at=values.get("last_attempt_at", "-"),
        last_watchdog_alert_month=values.get("last_watchdog_alert_month", "-"),
        last_watchdog_alert_at=values.get("last_watchdog_alert_at", "-"),
    )


def _isoformat(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat()
