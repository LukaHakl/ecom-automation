"""A form described in config, not hardcoded in Python.

Stage 3 fills a multi-tab product form on a marketplace admin UI. The obvious
implementation hardcodes that one marketplace's DOM, and it is the wrong one:
the DOM here changes, and the same shape -- tabs, fields, selectors, a save --
recurs on every other marketplace admin. So the form is *data*: a list of
``tab -> field -> selector -> value`` steps loaded from config, resolved against
a data row, and executed by a driver that knows nothing about which site it is
on.

That split is also what makes stage 3 testable at all. Resolving a plan is pure
and covered by tests; only :func:`execute` needs a browser, and it is small
enough to read in one sitting.

Config shape::

    form:
      steps:
        - tab: Info
          field: creator
          selector: "#creator-select"
          action: select_search      # type into the dropdown filter, then pick
          value: "{creator_value}"
          required: true
        - tab: Info
          field: title
          selector: "input[name=title]"
          action: fill
          value: "{title}"
        - tab: Pricing
          field: price
          selector: "input[name=price]"
          action: fill
          value: "{price}"

Value templates use ``str.format`` against the row, so ``{price}`` pulls the
row's price column. A template naming a column the row does not have is an
error at resolve time, not a mystery at runtime.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FILL, SELECT, SELECT_SEARCH, CLICK = "fill", "select", "select_search", "click"
ACTIONS = (FILL, SELECT, SELECT_SEARCH, CLICK)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


class FormSpecError(ValueError):
    """The form description in config is not usable."""


@dataclass
class Step:
    tab: str
    field: str
    selector: str
    action: str = FILL
    value: str = ""
    required: bool = False


@dataclass
class ResolvedStep:
    """A step with its value substituted, ready to execute."""

    tab: str
    field: str
    selector: str
    action: str
    value: str
    required: bool


def load_steps(spec: dict) -> list[Step]:
    """Validate and load the ``form.steps`` block from a config dict."""
    raw = (spec or {}).get("form", {}).get("steps")
    if not raw:
        raise FormSpecError(
            "config has no form.steps. Stage 3 drives the form from config so "
            "the DOM can change without a code edit -- see form_driver's "
            "docstring for the expected shape.")

    steps = []
    for index, entry in enumerate(raw):
        for key in ("tab", "field", "selector"):
            if not entry.get(key):
                raise FormSpecError(
                    "form.steps[%d] is missing %r" % (index, key))
        action = entry.get("action", FILL)
        if action not in ACTIONS:
            raise FormSpecError(
                "form.steps[%d] has action %r; expected one of %s"
                % (index, action, ", ".join(ACTIONS)))
        steps.append(Step(
            tab=entry["tab"], field=entry["field"], selector=entry["selector"],
            action=action, value=entry.get("value", ""),
            required=bool(entry.get("required", False)),
        ))
    return steps


def required_columns(steps: list[Step]) -> set[str]:
    """Every row column the steps reference, so a CSV can be checked up front."""
    return {name for step in steps for name in _PLACEHOLDER.findall(step.value)}


def resolve(steps: list[Step], row: dict) -> list[ResolvedStep]:
    """Substitute a data row into the steps.

    A step whose value resolves to empty is dropped **unless it is required**,
    in which case resolving raises. Silently skipping a required field produces
    a half-filled draft that looks like a successful run.
    """
    missing = required_columns(steps) - set(row)
    if missing:
        raise FormSpecError(
            "row is missing column(s) %s referenced by the form spec. Columns "
            "present: %s" % (", ".join(sorted(missing)), ", ".join(sorted(row))))

    resolved = []
    for step in steps:
        value = step.value.format(**row) if step.value else ""
        if not value.strip() and step.action != CLICK:
            if step.required:
                raise FormSpecError(
                    "required field %r resolved to empty for this row. Filling "
                    "the rest would produce a draft that looks complete."
                    % step.field)
            continue
        resolved.append(ResolvedStep(
            tab=step.tab, field=step.field, selector=step.selector,
            action=step.action, value=value, required=step.required,
        ))
    return resolved


def tabs_in_order(resolved: list[ResolvedStep]) -> list[str]:
    """Distinct tabs in first-appearance order.

    The driver switches tab only when it changes, so ordering the steps by tab
    in config avoids bouncing between them -- each switch is a page transition
    and a wait.
    """
    seen, order = set(), []
    for step in resolved:
        if step.tab not in seen:
            seen.add(step.tab)
            order.append(step.tab)
    return order


def describe(resolved: list[ResolvedStep]) -> str:
    """Human-readable plan, for ``--dry-run``.

    Dry-run output is the deliverable of a first run against a new CSV: it says
    what *would* be selected without touching the account.
    """
    lines = []
    current = None
    for step in resolved:
        if step.tab != current:
            current = step.tab
            lines.append("  [tab] %s" % current)
        lines.append("    %-14s %-10s %-28s = %s"
                     % (step.field, step.action, step.selector, step.value[:40]))
    return "\n".join(lines)
