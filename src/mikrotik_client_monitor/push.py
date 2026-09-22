"""Prometheus remote write, matching the direct push used by JK-BMS Cloud Bridge."""

from __future__ import annotations

import time

import requests
import snappy

from .collector import Snapshot
from .prometheus_pb2 import WriteRequest


def build_request(snapshot: Snapshot | None, router: str, last_success: float) -> WriteRequest:
    request = WriteRequest()
    timestamp_ms = int(time.time() * 1000)

    def add(name: str, value: float, labels: dict[str, str] | None = None) -> None:
        series = request.timeseries.add()
        for key, label_value in sorted({"__name__": name, "router": router, **(labels or {})}.items()):
            label = series.labels.add()
            label.name = key
            label.value = label_value
        sample = series.samples.add()
        sample.value = float(value)
        sample.timestamp = timestamp_ms

    add("mikrotik_collector_up", int(snapshot is not None))
    if last_success:
        add("mikrotik_collector_last_success_timestamp_seconds", last_success)
    if snapshot is None:
        return request
    add("mikrotik_fdb_entries", snapshot.fdb_entries)
    add("mikrotik_bound_leases", snapshot.bound_leases)
    add("mikrotik_unmatched_bound_leases", snapshot.unmatched_leases)
    add("mikrotik_ambiguous_macs", snapshot.ambiguous_macs)
    for (bridge, interface, ap), count in sorted(snapshot.counts.items()):
        add("mikrotik_clients_by_interface", count,
            {"bridge": bridge, "interface": interface, "ap": ap})
    for client in snapshot.clients:
        add("mikrotik_client_info", 1, {
            "mac": client.mac, "ip": client.ip, "hostname": client.hostname,
            "interface": client.interface, "ap": client.ap,
            "bridge": client.bridge, "vlan": client.vlan,
        })
    return request


def push_request(request: WriteRequest, url: str, user: str, token: str, timeout: int) -> None:
    payload = snappy.compress(request.SerializeToString())
    try:
        response = requests.post(
            url, data=payload, auth=(user, token), timeout=timeout,
            headers={
                "Content-Encoding": "snappy",
                "Content-Type": "application/x-protobuf",
                "X-Prometheus-Remote-Write-Version": "0.1.0",
                "User-Agent": "mikrotik-client-monitor/0.1",
            },
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Remote write transport failed ({type(exc).__name__})") from exc
    if response.status_code not in (200, 204):
        raise RuntimeError(f"Remote write failed (HTTP {response.status_code})")
