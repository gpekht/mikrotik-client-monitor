"""Read RouterOS detail output and turn bridge locations into client records."""

from __future__ import annotations

import ipaddress
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass


HOST_COMMAND = (
    "/interface bridge host print detail without-paging "
    "proplist=mac-address,on-interface,bridge,vid,local,invalid,disabled"
)
LEASE_COMMAND = (
    "/ip dhcp-server lease print detail without-paging "
    "proplist=status,active-mac-address,active-address,mac-address,address,host-name"
)
MAC_RE = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
ROW_RE = re.compile(r"^\s*\d+\s+(?:(?:[A-Z*]+)\s+)*(?=[\w.-]+=)")
KEY_RE = re.compile(r"[A-Za-z][\w.-]*")


class CollectionError(RuntimeError):
    """A snapshot could not be trusted."""


@dataclass(frozen=True)
class Client:
    mac: str
    ip: str
    hostname: str
    interface: str
    ap: str
    bridge: str
    vlan: str


@dataclass(frozen=True)
class Snapshot:
    clients: tuple[Client, ...]
    counts: dict[tuple[str, str, str], int]
    fdb_entries: int
    bound_leases: int
    unmatched_leases: int
    ambiguous_macs: int


def parse_detail(output: str) -> list[tuple[dict[str, str], str]]:
    """Parse RouterOS `print detail` rows, including wrapped and quoted fields."""
    output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output).replace("\r", "")
    if re.search(r"(?im)^\s*(?:failure:|syntax error|bad command name)", output):
        raise CollectionError("RouterOS rejected a read command")
    rows: list[tuple[dict[str, str], str]] = []
    current: list[str] = []
    prefix = ""
    for line in output.splitlines():
        match = ROW_RE.match(line)
        if match:
            if current:
                rows.append((_parse_fields(" ".join(current)), prefix))
            prefix = line[: match.end()]
            current = [line[match.end() :]]
        elif current and line.strip() and not line.lstrip().startswith(";;;"):
            current.append(line.strip())
    if current:
        rows.append((_parse_fields(" ".join(current)), prefix))
    header_only = all(not line.strip() or line.lstrip().startswith(("Flags:", "Columns:", "#"))
                      for line in output.splitlines())
    if output.strip() and not rows and not header_only:
        raise CollectionError("Unrecognized RouterOS output")
    return rows


def _parse_fields(value: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    pos = 0
    while pos < len(value):
        while pos < len(value) and value[pos].isspace():
            pos += 1
        if pos >= len(value):
            break
        key_match = KEY_RE.match(value, pos)
        if not key_match or key_match.end() >= len(value) or value[key_match.end()] != "=":
            raise CollectionError("Malformed RouterOS property output")
        key = key_match.group()
        pos = key_match.end() + 1
        if pos < len(value) and value[pos] == '"':
            pos += 1
            chars: list[str] = []
            while pos < len(value) and value[pos] != '"':
                if value[pos] == "\\" and pos + 1 < len(value):
                    pos += 1
                    if re.fullmatch(r"[0-9A-Fa-f]{2}", value[pos:pos + 2]):
                        chars.append(chr(int(value[pos:pos + 2], 16)))
                        pos += 2
                        continue
                chars.append(value[pos])
                pos += 1
            if pos >= len(value):
                raise CollectionError("Unterminated RouterOS quoted value")
            pos += 1
            fields[key] = "".join(chars)
        else:
            end = pos
            while end < len(value) and not value[end].isspace():
                end += 1
            fields[key] = value[pos:end]
            pos = end
    return fields


def _mac(value: str) -> str:
    return value.upper() if MAC_RE.fullmatch(value) else ""


def _ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def make_snapshot(host_output: str, lease_output: str,
                  interface_names: dict[str, str], device_names: dict[str, str]) -> Snapshot:
    hosts = parse_detail(host_output)
    leases = parse_detail(lease_output)
    locations: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for fields, prefix in hosts:
        flags = prefix.split()[1:]
        if fields.get("local") == "yes" or any("L" in flag for flag in flags) or fields.get("invalid") == "yes" or fields.get("disabled") == "yes":
            continue
        mac = _mac(fields.get("mac-address", ""))
        interface = fields.get("on-interface", "")
        if mac and interface:
            locations[mac].add((fields.get("bridge", ""), interface, fields.get("vid", "")))
    lease_map: dict[str, tuple[str, str]] = {}
    for fields, _ in leases:
        if fields.get("status") != "bound":
            continue
        mac = _mac(fields.get("active-mac-address") or fields.get("mac-address", ""))
        ip = _ip(fields.get("active-address") or fields.get("address", ""))
        if mac and ip:
            lease_map[mac] = (ip, fields.get("host-name", ""))
    counts: Counter[tuple[str, str, str]] = Counter()
    clients: list[Client] = []
    ambiguous = 0
    for mac, possible in sorted(locations.items()):
        if len(possible) != 1:
            ambiguous += 1
            continue
        bridge, interface, vlan = next(iter(possible))
        ap = interface_names.get(interface, interface)
        counts[(bridge, interface, ap)] += 1
        ip, dhcp_name = lease_map.get(mac, ("", ""))
        clients.append(Client(mac, ip, device_names.get(mac, dhcp_name), interface, ap, bridge, vlan))
    unmatched = sum(mac not in locations for mac in lease_map)
    return Snapshot(tuple(clients), dict(counts), sum(len(v) for v in locations.values()),
                    len(lease_map), unmatched, ambiguous)


def ssh_read(host: str, port: int, user: str, key_path: str,
             known_hosts: str, timeout: int, command: str) -> str:
    args = ["ssh", "-T", "-p", str(port), "-i", key_path,
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", f"ConnectTimeout={timeout}", "--", f"{user}@{host}", command]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 5, check=False)
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise CollectionError(f"SSH execution failed ({type(exc).__name__})") from exc
    if result.returncode:
        raise CollectionError(f"SSH read failed (exit {result.returncode})")
    return result.stdout
