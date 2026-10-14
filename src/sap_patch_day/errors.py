"""Typed error taxonomy.

Every message that a human reads comes from here or from ``config.py``, and the
exact configuration messages are part of the contract in ``docs/spec.md`` §8.
"""

from __future__ import annotations


class PatchDayError(Exception):
    """Base class for every error this application raises deliberately."""


class ConfigError(PatchDayError):
    """Configuration is absent, malformed or contradictory.

    Raised before any network request is made (spec §8).
    """


class FetchError(PatchDayError):
    """A page could not be retrieved after every allowed attempt."""


class ParseError(PatchDayError):
    """A page was retrieved but does not contain the expected structure."""


class DeliveryError(PatchDayError):
    """Amazon SES rejected the message."""
