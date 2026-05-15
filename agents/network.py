"""
agents/network.py — Shared network helpers (used by preflight and brain)

Extracted here so both can agree on how to reach the host without
duplicating logic or importing each other.
"""

import subprocess


def get_host_ip() -> str:
    """
    Detect the default gateway IP from the routing table.
    Returns the IP string, or raises RuntimeError if detection fails.
    """
    result = subprocess.run(
        ["ip", "route", "show", "default"],
        capture_output=True, text=True,
    )
    parts = result.stdout.split()
    try:
        return parts[parts.index("via") + 1]
    except (ValueError, IndexError):
        raise RuntimeError(
            f"Could not detect default gateway: {result.stdout!r}"
        )
