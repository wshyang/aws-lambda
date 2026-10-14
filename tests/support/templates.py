"""CloudFormation templates, read as data.

The templates use short-form intrinsics (`!Ref`, `!Sub`, `!If`, ...) that plain
`yaml.safe_load` refuses. `load_template` keeps them as readable text — a scalar
`!Sub "<text>"` becomes the string `"!Sub <text>"` — which is all the parity and
invariant tests need to compare names and wiring.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "infrastructure" / "cloudformation"
APP_TEMPLATE = TEMPLATE_DIR / "sap-security-patch-day-notifier.yaml"
BUCKET_TEMPLATE = TEMPLATE_DIR / "artifacts-bucket.yaml"

PREFIX_SUB = "!Sub "


class _ShortFormLoader(yaml.SafeLoader):
    """SafeLoader that keeps CloudFormation short-form intrinsics readable."""


def _keep_short_form(loader: yaml.SafeLoader, tag: str, node: yaml.Node) -> object:
    name = tag if tag.startswith("!") else f"!{tag}"
    if isinstance(node, yaml.ScalarNode):
        scalar: str = loader.construct_scalar(node)
        return f"{name} {scalar}"
    if isinstance(node, yaml.SequenceNode):
        return {name: loader.construct_sequence(node, deep=True)}
    return {name: loader.construct_mapping(node, deep=True)}


_ShortFormLoader.add_multi_constructor("!", _keep_short_form)


def load_template(path: Path) -> Mapping[str, object]:
    """Parse a template; every intrinsic survives as text or as a single key."""
    text = path.read_text(encoding="utf-8")
    loaded = yaml.load(text, Loader=_ShortFormLoader)  # noqa: S506 - a SafeLoader subclass
    assert isinstance(loaded, dict), f"{path} is not a YAML mapping"
    return loaded


def resources(document: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    found = document["Resources"]
    assert isinstance(found, dict)
    return found


def parameters(document: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    found = document["Parameters"]
    assert isinstance(found, dict)
    return found


def sub_text(value: object) -> str:
    """The literal part of a `!Sub "<text>"` value."""
    text = str(value)
    assert text.startswith(PREFIX_SUB), f"expected a !Sub value, got {text!r}"
    return text[len(PREFIX_SUB) :]


def references(value: object) -> Iterator[str]:
    """Every `!Ref NAME` found anywhere inside a template fragment.

    A scalar `!Ref X` survives as the string `"!Ref X"`; a `!Ref` nested inside
    a sequence or mapping survives as a `{"!Ref": "X"}` entry. Both are read.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "!Ref":
                yield str(item)
            else:
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)
    elif isinstance(value, str) and value.startswith("!Ref "):
        yield value[len("!Ref ") :]


def resources_of_type(
    document: Mapping[str, object], type_name: str
) -> Mapping[str, Mapping[str, object]]:
    return {
        name: resource
        for name, resource in resources(document).items()
        if resource.get("Type") == type_name
    }
