"""SAP Security Patch Day notifier.

A single-purpose AWS Lambda that detects the publication of SAP's monthly
Security Patch Day bulletin and emails the notes table to a configured list.

The deployed artefact is this package only: no third-party dependencies at
runtime. See ``docs/spec.md`` for the functional contract and
``docs/addenda/0003-runtime-and-dependency-policy.md`` for the dependency rule.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "1.0.0"
