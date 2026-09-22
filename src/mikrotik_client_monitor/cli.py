"""Systemd friendly one-shot and periodic entry points."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .collector import (CollectionError, HOST_COMMAND, LEASE_COMMAND,
                        make_snapshot, ssh_read)
from .push import build_request, push_request

LOG = logging.getLogger("mikrotik_client_monitor")


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    user: str
    key_path: str
    known_hosts: str
    ssh_timeout: int
    interval: int
    router_label: str
    interface_names: dict[str, str]
    device_names: dict[str, str]
    grafana_url: str
    grafana_user: str
    grafana_token: str
    http_timeout: int


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name}")
    return value


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _mapping(name: str) -> dict[str, str]:
    try:
        data = json.loads(os.getenv(name, "{}"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {name}") from exc
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                         for k, v in data.items()):
        raise ValueError(f"{name} must be a JSON object of strings")
    return data


def config_from_env() -> Config:
    host, user = _required("ROUTER_HOST"), _required("ROUTER_USER")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", host) or host.startswith("-"):
        raise ValueError("Invalid ROUTER_HOST")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", user) or user.startswith("-"):
        raise ValueError("Invalid ROUTER_USER")
    url = _required("GRAFANA_URL")
    if not url.startswith("https://"):
        raise ValueError("GRAFANA_URL must use HTTPS")
    names = _mapping("DEVICE_NAMES_JSON")
    if any(not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac) for mac in names):
        raise ValueError("DEVICE_NAMES_JSON keys must be MAC addresses")
    return Config(
        host=host, port=_integer("ROUTER_PORT", 22, 1, 65535), user=user,
        key_path=_required("SSH_KEY_PATH"), known_hosts=_required("SSH_KNOWN_HOSTS"),
        ssh_timeout=_integer("SSH_TIMEOUT_SECONDS", 15, 1, 120),
        interval=_integer("POLL_INTERVAL_SECONDS", 120, 10, 86400),
        router_label=_required("ROUTER_LABEL"),
        interface_names=_mapping("INTERFACE_NAMES_JSON"),
        device_names={mac.upper(): name for mac, name in names.items()},
        grafana_url=url, grafana_user=_required("GRAFANA_USER"),
        grafana_token=_required("GRAFANA_TOKEN"),
        http_timeout=_integer("HTTP_TIMEOUT_SECONDS", 15, 1, 120),
    )


def collect(config: Config):
    base = (config.host, config.port, config.user, config.key_path,
            config.known_hosts, config.ssh_timeout)
    host_output = ssh_read(*base, HOST_COMMAND)
    lease_output = ssh_read(*base, LEASE_COMMAND)
    return make_snapshot(host_output, lease_output,
                         config.interface_names, config.device_names)


def cycle(config: Config, last_success: float = 0) -> tuple[bool, float]:
    snapshot = None
    try:
        snapshot = collect(config)
        last_success = time.time()
        LOG.info("Collected %d clients on %d interfaces",
                 len(snapshot.clients), len(snapshot.counts))
    except CollectionError as exc:
        LOG.error("Router collection failed: %s", exc)
    request = build_request(snapshot, config.router_label, last_success)
    push_request(request, config.grafana_url, config.grafana_user,
                 config.grafana_token, config.http_timeout)
    LOG.info("Pushed %d metric series", len(request.timeseries))
    return snapshot is not None, last_success


def main() -> int:
    parser = argparse.ArgumentParser(description="MikroTik client location monitor")
    parser.add_argument("mode", choices=("once", "run"), help="one poll or periodic polling")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.env_file.exists():
        load_dotenv(args.env_file, override=False)
    try:
        config = config_from_env()
    except ValueError as exc:
        LOG.error("Configuration error: %s", exc)
        return 2
    last_success = 0.0
    while True:
        started = time.monotonic()
        try:
            ok, last_success = cycle(config, last_success)
        except RuntimeError as exc:
            LOG.error("Metric push failed: %s", exc)
            ok = False
        if args.mode == "once":
            return 0 if ok else 1
        time.sleep(max(0, config.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
