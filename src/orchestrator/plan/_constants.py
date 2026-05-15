_STATUS_CLASS: dict[str, str] = {
    "pending": "pending",
    "passed": "complete",
    "blocked": "blocked",
    "failed": "blocked",
    "in_progress": "active",
    "skipped": "skipped",
}

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
]

_DURATION_COLORS: list[tuple[int | None, str]] = [
    (30, "#22c55e"),  # < 30s  — green
    (120, "#a3e635"),  # < 2m   — lime
    (300, "#fbbf24"),  # < 5m   — amber
    (900, "#f97316"),  # < 15m  — orange
    (None, "#ef4444"),  # ≥ 15m  — red
]
