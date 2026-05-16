_STATUS_CLASS: dict[str, str] = {
    "pending": "pending",
    "passed": "complete",
    "blocked": "blocked",
    "failed": "blocked",
    "in_progress": "active",
    "skipped": "skipped",
}

# Terminal-status precedence: lower numbers win when aggregating multiple
# statuses (e.g. parent vs children, round-1 sub-node vs final-cycle outcome).
# Ordering — failed (runner/infra error) is the strongest signal that something
# is wrong; blocked covers terminal "won't complete" states; changes-requested
# is a review verdict that requires a fix cycle to resolve; in_progress / passed
# / skipped / pending represent the run forward to nothing-to-show. The numeric
# gap between values has no meaning; only the order matters. See ADR-026.
_STATUS_PRECEDENCE: dict[str, int] = {
    "failed": 0,
    "blocked": 1,
    "changes-requested": 2,
    "in_progress": 3,
    "passed": 4,
    "skipped": 5,
    "pending": 6,
}


def worst_status(*statuses: str) -> str:
    """Return the highest-precedence (worst) status among the given values.

    Unknown statuses sort after everything in the table — they never beat a
    known status because we'd rather render a recognised state than propagate
    a typo. Empty input returns ``"pending"``.
    """
    if not statuses:
        return "pending"
    sentinel = max(_STATUS_PRECEDENCE.values()) + 1
    return min(statuses, key=lambda s: _STATUS_PRECEDENCE.get(s, sentinel))


_STATUS_ICON: dict[str, str] = {
    "pending": "-",
    "passed": "✅",
    "blocked": "🔴",
    "failed": "🔴",
    "in_progress": "⏳",
    "skipped": "-",
}

_CLASSDEFS = [
    "    classDef complete fill:#059669,color:#fff,stroke:none",
    "    classDef active fill:#d97706,color:#fff,stroke:none",
    "    classDef pending fill:#6b7280,color:#fff,stroke:#4b5563",
    "    classDef blocked fill:#dc2626,color:#fff,stroke:none",
    "    classDef skipped fill:#4b5563,color:#9ca3af,stroke:#374151",
    "    classDef gate fill:#92400e,color:#fff,stroke:#d97706,stroke-width:2px",
    "    classDef fannode fill:#374151,color:#9ca3af,stroke:#1f2937,stroke-width:1px",
    "    classDef startend fill:#4f46e5,color:#fff,stroke:none",
    "    classDef input fill:#1e3a8a,color:#dbeafe,stroke:#3b82f6,stroke-width:1px",
    "    classDef json fill:#111827,color:#d1d5db,stroke:#374151,stroke-width:1px",
]

_DURATION_COLORS: list[tuple[int | None, str]] = [
    (30, "#22c55e"),  # < 30s  — green
    (120, "#a3e635"),  # < 2m   — lime
    (300, "#fbbf24"),  # < 5m   — amber
    (900, "#f97316"),  # < 15m  — orange
    (None, "#ef4444"),  # ≥ 15m  — red
]
