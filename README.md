# MikroTik Client Monitor

Small, standalone service that reads a MikroTik router's bridge forwarding database (FDB) and DHCP leases over SSH, joins them by MAC address, and sends Prometheus metrics directly to a remote write endpoint such as Grafana Cloud. It runs on any Linux host with Python 3.10+, including a Raspberry Pi. It has no dependency on a separate BMS project or a local Prometheus/Alloy installation.

The bridge FDB shows the router-facing interface through which a MAC was learned. If multiple APs share that path, the router cannot identify the exact AP or Wi-Fi association. `ap` is your friendly name for that interface, not a wireless registration reading. Devices without a bound DHCP lease still appear, with empty IP and hostname labels. Local bridge MACs are excluded. A MAC learned at multiple locations is excluded from client/location metrics and counted as ambiguous.

## Metrics

| Metric | Meaning |
| --- | --- |
| `mikrotik_client_info{router,mac,ip,hostname,interface,ap,bridge,vlan}` | One series per currently learned client; value `1` |
| `mikrotik_clients_by_interface{router,bridge,interface,ap}` | Learned client count by bridge interface |
| `mikrotik_fdb_entries{router}` | Distinct learned FDB locations after excluding local entries |
| `mikrotik_bound_leases{router}` | Bound leases with valid MAC and IP |
| `mikrotik_unmatched_bound_leases{router}` | Bound leases whose MAC is absent from the FDB |
| `mikrotik_ambiguous_macs{router}` | MACs learned in multiple locations |
| `mikrotik_collector_up{router}` | `1` when SSH collection succeeded, `0` when it failed |
| `mikrotik_collector_last_success_timestamp_seconds{router}` | Last successful collection time |

Client MAC, IP, and hostname are metric labels. They leave your network for the configured remote write service and can create many time series on busy networks. Configure retention/access accordingly. Rename devices with `DEVICE_NAMES_JSON` if their DHCP hostname is missing or unhelpful. Private/randomized MACs appear as different devices when they change.

## Router setup

Use a custom RouterOS user group with only SSH login and read rights; the built-in `read` group grants additional permissions. From an administrative router session:

```routeros
/user group add name=metrics-read policy=ssh,read
/user add name=metrics-reader group=metrics-read password="<temporary-strong-password>"
/user ssh-keys add user=metrics-reader key="<contents-of-public-key>"
```

RouterOS versions that do not support `ssh-keys add` can upload the **public** key and use `/user ssh-keys import public-key-file=<filename> user=metrics-reader`. Never upload or commit the private key. Limit SSH access to the collector host in the router firewall or `/ip service` rules. Check your RouterOS version's support for your key type. Test the two read commands as this user before enabling the service:

```sh
ssh -i /path/to/private/key metrics-reader@router.example.net '/interface bridge host print detail without-paging proplist=mac-address,on-interface,bridge,vid,local,invalid,disabled'
ssh -i /path/to/private/key metrics-reader@router.example.net '/ip dhcp-server lease print detail without-paging proplist=status,active-mac-address,active-address,mac-address,address,host-name'
```

On the Linux host, generate a dedicated SSH key (`ssh-keygen -t ed25519 -f /path/to/private/key`), add its public part on RouterOS, and pin the router's host key in a dedicated `known_hosts` file. Verify the host key fingerprint through an independent trusted route before trusting it. The collector uses strict host key checking and never accepts a changed or unknown key automatically. It supports SSH keys only; no password goes into a process command line or source file.

## Install and configure

```sh
python3 -m venv .venv
.venv/bin/pip install .
cp .env.example .env
chmod 600 .env
# Edit .env with your router, SSH, and remote write details.
.venv/bin/mikrotik-client-monitor once
```

`GRAFANA_URL`, `GRAFANA_USER`, and `GRAFANA_TOKEN` are the Prometheus remote write URL, metrics instance ID, and a metrics write access token from your Grafana Cloud stack. They use the same direct protobuf + Snappy + Basic Auth method as JK-BMS Cloud Bridge. The URL must use HTTPS. The collector accepts any compatible Prometheus remote write endpoint using this authentication. The sample `.env.example` contains documentation-only placeholders. `.env`, key files, and host key files are ignored by Git.

`INTERFACE_NAMES_JSON` maps RouterOS interface names to friendly labels. Unmapped interfaces use their RouterOS names. `DEVICE_NAMES_JSON` maps MAC addresses to persistent friendly names; DHCP hostname is the fallback. `ROUTER_LABEL` identifies a router when several collectors write to one metrics backend. `POLL_INTERVAL_SECONDS` controls `run` mode. `once` makes one collection/push and exits, suitable for a systemd timer.

The collector does not need to expose a local port. On SSH failure it pushes `mikrotik_collector_up=0` and exits nonzero in `once` mode. On remote write failure it exits nonzero; it does not log credentials or HTTP response bodies. Check the systemd journal for failures.

## systemd on Linux / Raspberry Pi

The supplied units assume the project is installed in `/opt/mikrotik-client-monitor`, with a `.venv` there, a dedicated `mikrotik-monitor` service account, and environment settings in `/etc/mikrotik-client-monitor.env` owned by root with mode `0600`. Give the service account read access to its SSH private key and pinned `known_hosts`. Adjust paths and account names for your host.

For a continuously running service, copy `systemd/mikrotik-client-monitor.service` to `/etc/systemd/system/`, then run:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now mikrotik-client-monitor.service
sudo journalctl -u mikrotik-client-monitor.service -f
```

For one-shot execution, install `mikrotik-client-monitor-once.service` and `mikrotik-client-monitor.timer`, then enable the timer. Its example interval is two minutes; edit `OnUnitActiveSec` to your desired schedule and reload systemd. Use either the continuous service or the timer, not both.

## Grafana examples

Use your Prometheus data source. For a table of clients observed recently, use an instant query with the freshness filter below. Adjust `180` seconds to exceed your polling interval but stay below your desired disappearance delay:

```promql
mikrotik_client_info and (time() - timestamp(mikrotik_client_info) < 180)
```

In Grafana's table transformations, show the `hostname`, `ip`, `mac`, `ap`, `interface`, and `router` labels as columns. A blank hostname or IP means the device was learned by the bridge but has no matching bound lease. For counts by AP/interface:

```promql
mikrotik_clients_by_interface
```

For a live count panel, filter out series that have not been refreshed recently:

```promql
mikrotik_clients_by_interface and (time() - timestamp(mikrotik_clients_by_interface) < 180)
```

For collector health:

```promql
mikrotik_collector_up
```

Remote write sends fresh series at each poll. Old client/location series age out under your Prometheus backend's lookback window; the freshness filter gives a more accurate live table after clients move or disappear. Historical queries retain the earlier locations. FDB entries themselves can age out when a device is quiet, so this is an observation of recently learned paths rather than a complete inventory of powered-on devices.

## Development

```sh
python3 -m unittest discover -s tests -v
```

The parser tests use synthetic RouterOS detail output. A live router and a Grafana Cloud account are not required for the tests.

RouterOS command and privilege references: [CLI print options](https://help.mikrotik.com/docs/spaces/ROS/pages/328134/Command%2BLine%2BInterface), [bridge host properties](https://help.mikrotik.com/docs/spaces/ROS/pages/328068/Bridging%2Band%2BSwitching), [RouterOS users and SSH keys](https://help.mikrotik.com/docs/spaces/ROS/pages/8978504/User). Grafana reference: [Prometheus remote write integration](https://grafana.com/docs/grafana-cloud/observe-and-act/send-data/metrics/metrics-prometheus/prometheus-config-examples/integration-guide/).
