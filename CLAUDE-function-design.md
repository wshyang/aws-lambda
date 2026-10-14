# CLAUDE-function-design.md — function and module design rules

Derived from `GREENFIELD.md` §7 (Martin Fowler, *Refactoring*, 2nd ed.) and
stated here in the form this repository enforces. The upstream template
references this file by name; it is the authoritative version for this project.

## 1. Function size and extraction

- **≤ 20 lines** target per function body; **50 lines hard limit**.
- If a comment is needed to explain a block, that block becomes a named
  function.
- One function does one thing at one level of abstraction. Orchestration and
  implementation detail never share a function body.

## 2. Guard clauses over nesting

- Exit early; never nest the happy path. Maximum nesting depth is **3 levels**.
- Parse, validate and coerce at the top of the function; the rest of the body
  then works on known-good inputs.

```python
# yes
def parse_priority(text: str) -> str:
    cleaned = text.strip()
    if cleaned in KNOWN_PRIORITIES:
        return cleaned
    return cleaned or UNKNOWN_PRIORITY
```

## 3. Parameter discipline

- **≤ 3 parameters.** Beyond that, introduce a frozen dataclass parameter
  object (`HttpPolicy`, `SendRequest`, `RuntimeConfig`).
- **No boolean/flag parameters.** A flag is a second function wearing a
  trench coat: split it, or inject a different port implementation
  (the dry-run switch in this repository is a *port*, not a flag — see
  `sap_patch_day.handler.build_ports`).
- Preserve the whole object: pass the domain object, not its extracted fields.

## 4. Naming

- Names reveal intent; rename freely as understanding improves.
- Booleans read as `is_`, `has_`, `should_`, `can_` (`is_published`,
  `has_attachment`).
- Functions are verb + noun (`parse_notes_table`, `render_subject`).
- No abbreviations in domain code: `recipients`, not `rcpts`; `configurations`,
  not `cfg`.

## 5. Encapsulation and mutation

- **Never mutate in place.** Produce a new object — `dataclasses.replace(...)`
  or a new dataclass instance. Every model in `sap_patch_day.models` is
  `frozen=True`.
- **Separate Query from Modifier.** A function either returns a value or has a
  side effect, never both. `check_publication` returns facts; `deliver_bulletin`
  performs effects and returns nothing but the provider's message id.
- Mutable state is encapsulated: the state store is an object with methods, not
  a dict threaded through the codebase.

## 6. Module and directory structure

- Group by **domain feature**, not by layer type. Here:
  `bulletin_index.py` (index page), `notes_table.py` + `note_fields.py`
  (month page), `htmltree.py` (HTML document model), `email_render.py`,
  `mailer.py`, `config.py`, `state.py`, `handler.py`.
- Files ≤ 400 LOC typical, **800 LOC hard limit**; the limit is asserted by
  `tests/integration/test_module_budget.py`, so it cannot silently drift.
- When a file must be split, use the **barrel pattern**: extract per-domain
  files and re-export from the original module so call sites do not change.
- No circular imports between peer modules; the dependency direction is
  `handler → {bulletin, render, deliver} → {models, parsers, ports}`.

## 7. Checkable gates (what CI actually asserts)

| Rule | Gate |
| ---- | ---- |
| Function length ≤ 50 lines | `ruff` `PLR0915` (max-statements) + code review for the 20-line target |
| Nesting ≤ 3 (cyclomatic complexity ≤ 10) | `ruff` `C901`, `PLR0912` |
| ≤ 3 parameters | `ruff` `PLR0913` (`max-args = 3`) |
| No boolean flag parameters | `ruff` `FBT001`–`FBT003` (flake8-boolean-trap) |
| No mutation of arguments, no mutable class attributes | `ruff` `RUF012`, review; models are `frozen=True` |
| No ambiguous/abbreviated names | `ruff` `N` (pep8-naming) with the project's ignores documented in `pyproject.toml` |
| File ≤ 800 LOC | `tests/integration/test_module_budget.py` |
| One concern per file | review; file names are the contract |
| Logic not buried in string templates | email HTML is built by small `_render_*` functions, each unit-tested |

The 20-line *target* is not machine-gated — a 30-line function that reads as
one coherent unit is accepted at review; a 51-line function is not.
