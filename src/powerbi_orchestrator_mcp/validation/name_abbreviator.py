"""Shorten the names that are too long to read.

This is the analysis behind the "abreviar" tool. A semantic model
accumulates names the way a codebase does: ``Total Revenue Amount In
Local Currency`` becomes ``Total Revenue Amount In Local Currency
Including Tax For The Fiscal Year``, and every measure that inherits
the habit carries it.

Long names are not only ugly. They make the DAX unreadable, they eat
the space in a report field, and they are the reason a model has to be
renamed before it can be published anywhere.

The proposal is mechanical and reversible:

- a word that has a standard abbreviation is shortened to it;
- a filler word that carries no meaning is dropped;
- anything else is left alone.

A name that cannot be shortened enough without changing what it means
is reported as needing a name by hand, rather than truncated into
something that no longer says what it said. And a proposal that would
collide with an existing name is adjusted, and says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "NAME_ABBREVIATIONS",
    "FILLER_WORDS",
    "NameProposal",
    "propose_shorter_names",
]

#: The standard abbreviation for the words that get long.
NAME_ABBREVIATIONS: dict[str, str] = {
    "amount": "Amt",
    "quantity": "Qty",
    "revenue": "Rev",
    "description": "Desc",
    "customer": "Cust",
    "product": "Prod",
    "category": "Cat",
    "percentage": "Pct",
    "percent": "Pct",
    "number": "Num",
    "value": "Val",
    "balance": "Bal",
    "discount": "Disc",
    "inventory": "Inv",
    "transaction": "Txn",
    "country": "Ctry",
    "department": "Dept",
    "effective": "Eff",
    "currency": "Cur",
    "local": "Loc",
    "including": "Incl",
    "excluding": "Excl",
    "adjusted": "Adj",
    "forecast": "Fcst",
    "average": "Avg",
    "minimum": "Min",
    "maximum": "Max",
    "count": "Cnt",
    "identifier": "Id",
    "annual": "Ann",
    "weekly": "Wk",
    "daily": "Day",
    "previous": "Prev",
    "current": "Curr",
    "total": "Tot",
}

#: The words that carry no meaning on their own.
FILLER_WORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "per",
        "the",
        "to",
        "with",
    }
)

_VALID_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class NameProposal:
    """One name, and what could be done with it."""

    name: str
    kind: str  # measure | column
    table: str | None = None
    proposed: str | None = None
    """The shorter name, when one could be found."""
    reason: str = ""
    """What the change is, in one line."""
    changes: list[dict[str, str]] = field(default_factory=list)
    """Each word that was changed, and how."""
    length_before: int = 0
    length_after: int = 0
    needs_a_name_by_hand: bool = False
    """True when no safe shortening was found.

    The name is too long to shorten mechanically without changing what
    it means, so a person has to decide.
    """
    collision_resolved: bool = False
    """True when the proposal was adjusted to avoid a collision."""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "table": self.table,
            "proposed": self.proposed,
            "reason": self.reason,
            "changes": self.changes,
            "length_before": self.length_before,
            "length_after": self.length_after,
            "needs_a_name_by_hand": self.needs_a_name_by_hand,
            "collision_resolved": self.collision_resolved,
        }


def _split_words(name: str) -> list[str]:
    """The words of a name, keeping the separator style.

    A name with spaces keeps spaces; a name with underscores keeps
    underscores. Mixing them is not something a rename should do
    quietly.
    """
    if " " in name:
        return name.split(" ")
    if "_" in name:
        return name.split("_")
    return [name]


def _join_words(words: list[str], original: str) -> str:
    if " " in original:
        return " ".join(words)
    if "_" in original:
        return "_".join(words)
    return "".join(words)


def _shorten(
    name: str,
    *,
    max_length: int,
    abbreviate_words: bool,
    drop_filler_words: bool,
) -> tuple[str | None, list[dict[str, str]]]:
    """The shorter form of a name, and what changed."""
    words = _split_words(name)
    changes: list[dict[str, str]] = []
    result: list[str] = []

    for word in words:
        bare = word.strip("[]").strip()
        key = bare.lower()

        if drop_filler_words and key in FILLER_WORDS and len(result) < len(words) - 1:
            changes.append({"from": word, "to": "", "rule": "dropped a filler word"})
            continue

        if abbreviate_words and key in NAME_ABBREVIATIONS:
            replacement = NAME_ABBREVIATIONS[key]
            if replacement.lower() != key:
                if word[:1].isupper():
                    replacement = replacement[:1].upper() + replacement[1:]
                changes.append({"from": word, "to": replacement, "rule": "standard abbreviation"})
                result.append(replacement)
                continue

        result.append(word)

    if not changes:
        return None, []

    shortened = _join_words(result, name)
    if len(shortened) > max_length:
        # It is still too long, and the only way to make it shorter
        # would be to change what it says.
        return None, changes

    return shortened, changes


def _is_valid(name: str) -> bool:
    """True when the name can be written as an identifier."""
    bare = name.strip("[]")
    if not bare:
        return False
    if " " in bare:
        return all(part and (part[0].isalpha() or part[0] == "_") for part in bare.split())
    return bool(_VALID_IDENTIFIER.match(bare))


def _disambiguate(name: str, used: set[str]) -> str:
    """Add a counter until the name is free."""
    base = name
    counter = 2
    while f"{base}{counter}".lower() in used:
        counter += 1
    return f"{base}{counter}"


def propose_shorter_names(
    names: dict[str, str],
    *,
    kind: str = "measure",
    table: str | None = None,
    max_length: int = 40,
    abbreviate_words: bool = True,
    drop_filler_words: bool = True,
    taken: set[str] | None = None,
) -> list[NameProposal]:
    """Propose a shorter name for each name that is too long.

    Args:
        names: The names to consider.
        kind: ``measure`` or ``column``.
        table: The table the names belong to.
        max_length: Above this length, a name is a candidate.
        abbreviate_words: Shorten the words that have a standard
            abbreviation.
        drop_filler_words: Drop the words that carry no meaning.
        taken: The names already in use, so a proposal does not collide
            with one of them.

    Returns:
        One proposal per name, in the order they were given.
    """
    used = {value.lower() for value in (taken or set())}
    proposals: list[NameProposal] = []

    for name in names:
        proposal = NameProposal(
            name=name,
            kind=kind,
            table=table,
            length_before=len(name),
        )
        if len(name) <= max_length:
            proposal.reason = f"it is {len(name)} characters, within the limit"
            proposals.append(proposal)
            continue

        shortened, changes = _shorten(
            name,
            max_length=max_length,
            abbreviate_words=abbreviate_words,
            drop_filler_words=drop_filler_words,
        )

        if shortened is None:
            proposal.needs_a_name_by_hand = True
            proposal.length_after = len(name)
            proposal.reason = (
                "it is too long and cannot be shortened without changing what it means"
            )
            proposal.changes = changes
            proposals.append(proposal)
            continue

        if not _is_valid(shortened):
            proposal.needs_a_name_by_hand = True
            proposal.length_after = len(name)
            proposal.reason = "the shortened name would not be a valid identifier"
            proposal.changes = changes
            proposals.append(proposal)
            continue

        # Do not collide with a name that already exists, nor with
        # another proposal. The name being shortened is not in the way:
        # it is going away.
        occupied = used - {name.lower()}
        candidate = shortened
        if candidate.lower() in occupied:
            candidate = _disambiguate(candidate, occupied)
            proposal.collision_resolved = True
        used.add(candidate.lower())

        proposal.proposed = candidate
        proposal.changes = changes
        proposal.length_after = len(candidate)
        proposal.reason = f"it was {len(name)} characters; this one is {len(candidate)}"
        proposals.append(proposal)

    return proposals
