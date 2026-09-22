"""Human-readable snapshot output for an interactive terminal."""

from __future__ import annotations

from .collector import Snapshot


def _safe(value: str, fallback: str = "-") -> str:
    cleaned = "".join(char if char.isprintable() else "?" for char in value).strip()
    return cleaned or fallback


def render_snapshot(snapshot: Snapshot) -> str:
    """Group the current FDB clients by router-facing interface."""
    lines: list[str] = []
    for (bridge, interface, ap), count in sorted(snapshot.counts.items()):
        heading = f"{_safe(ap)} ({_safe(interface)})"
        if bridge:
            heading += f" [bridge: {_safe(bridge)}]"
        lines.extend((f"=== {heading}: {count} clients ===",
                      f"{'IP':<39}  {'MAC':<17}  {'VLAN':<4}  NAME"))
        clients = (client for client in snapshot.clients
                   if (client.bridge, client.interface, client.ap) == (bridge, interface, ap))
        for client in sorted(clients, key=lambda item: (item.hostname.casefold(), item.ip, item.mac)):
            lines.append(f"{_safe(client.ip):<39}  {client.mac:<17}  "
                         f"{_safe(client.vlan):<4}  {_safe(client.hostname, '<unknown>')}")
        lines.append("")
    if not lines:
        lines.append("No bridge clients found.")
    lines.append(f"Total: {len(snapshot.clients)} clients; "
                 f"{snapshot.bound_leases} bound leases; "
                 f"{snapshot.unmatched_leases} unmatched leases; "
                 f"{snapshot.ambiguous_macs} ambiguous MACs")
    return "\n".join(lines).rstrip() + "\n"
