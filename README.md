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

## Complete setup on Raspberry Pi OS or Debian Linux

These commands use a dedicated `mikrotik-monitor` Linux account, install the project in `/opt/mikrotik-client-monitor`, and run the collector with systemd. Replace example hostnames, usernames, paths, and mappings with your own values **only on the Linux host or router**. Keep them out of the Git repository.

### 1. Install the project and create the service account

```sh
sudo apt update
sudo apt install -y git python3 python3-venv openssh-client
sudo useradd --system --home-dir /var/lib/mikrotik-client-monitor --create-home --shell /usr/sbin/nologin mikrotik-monitor
sudo git clone https://github.com/gpekht/mikrotik-client-monitor.git /opt/mikrotik-client-monitor
sudo python3 -m venv /opt/mikrotik-client-monitor/.venv
sudo /opt/mikrotik-client-monitor/.venv/bin/pip install /opt/mikrotik-client-monitor
```

### 2. Generate a dedicated SSH key on the Linux host

```sh
sudo install -d -o mikrotik-monitor -g mikrotik-monitor -m 0700 /var/lib/mikrotik-client-monitor/.ssh
sudo -u mikrotik-monitor ssh-keygen -q -t ed25519 -N '' -f /var/lib/mikrotik-client-monitor/.ssh/id_ed25519
sudo cat /var/lib/mikrotik-client-monitor/.ssh/id_ed25519.pub
```

Copy the **public** key printed by the last command. The private key stays on this host; never upload it to the router or commit it. A key without a passphrase is used so systemd can run unattended, so keep the service account and key file permissions restricted.

### 3. Create a least-privilege RouterOS user

In an administrative RouterOS terminal, create a custom group with only `ssh,read` policies, then add the public key from step 2:

```routeros
/user group add name=metrics-read policy=ssh,read
/user add name=metrics-reader group=metrics-read password="<unique-strong-password>"
/user ssh-keys add user=metrics-reader key="<paste-the-public-ssh-key>"
```

The built-in `read` group grants more permissions than this collector needs. RouterOS versions without `ssh-keys add` can upload the **public** key and use `/user ssh-keys import public-key-file=<filename> user=metrics-reader`. Restrict router SSH access to the collector host in your router firewall or `/ip service` rules. Use a key type supported by your RouterOS version. The collector uses SSH keys only, not password login.

### 4. Pin the router's SSH host key and test both reads

On the Linux host, set these two shell variables for the commands below. Use the router's actual hostname or address and SSH port:

```sh
ROUTER_ADDRESS=router.example.net
ROUTER_SSH_PORT=22
ssh-keyscan -p "$ROUTER_SSH_PORT" "$ROUTER_ADDRESS" > /tmp/mikrotik-host-key
ssh-keygen -lf /tmp/mikrotik-host-key
```

Compare the displayed fingerprint with the router host key obtained through a separately trusted administrative connection **before** installing it. Once it matches:

```sh
sudo install -o mikrotik-monitor -g mikrotik-monitor -m 0600 /tmp/mikrotik-host-key /var/lib/mikrotik-client-monitor/.ssh/known_hosts
rm /tmp/mikrotik-host-key
sudo -u mikrotik-monitor ssh -T -p "$ROUTER_SSH_PORT" -i /var/lib/mikrotik-client-monitor/.ssh/id_ed25519 -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/var/lib/mikrotik-client-monitor/.ssh/known_hosts "metrics-reader@$ROUTER_ADDRESS" '/interface bridge host print detail without-paging proplist=mac-address,on-interface,bridge,vid,local,invalid,disabled'
sudo -u mikrotik-monitor ssh -T -p "$ROUTER_SSH_PORT" -i /var/lib/mikrotik-client-monitor/.ssh/id_ed25519 -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/var/lib/mikrotik-client-monitor/.ssh/known_hosts "metrics-reader@$ROUTER_ADDRESS" '/ip dhcp-server lease print detail without-paging proplist=status,active-mac-address,active-address,mac-address,address,host-name'
```

Both commands should return RouterOS property rows without a password prompt or host key warning. The collector also uses strict host key checking and will reject an unknown or changed router key.

### 5. Add the Grafana Cloud settings

In Grafana Cloud, open your stack's **Details → Prometheus → Details** and copy the remote write URL and metrics instance ID. Create a Cloud Access Policy token with `metrics:write` scope, or reuse a token already scoped for writing metrics. Put these values in a root-owned environment file on the Linux host:

```sh
sudo install -o root -g root -m 0600 /opt/mikrotik-client-monitor/.env.example /etc/mikrotik-client-monitor.env
sudoedit /etc/mikrotik-client-monitor.env
```

Set `ROUTER_HOST`, `ROUTER_PORT`, `ROUTER_USER`, `SSH_KEY_PATH=/var/lib/mikrotik-client-monitor/.ssh/id_ed25519`, `SSH_KNOWN_HOSTS=/var/lib/mikrotik-client-monitor/.ssh/known_hosts`, `GRAFANA_URL`, `GRAFANA_USER`, and `GRAFANA_TOKEN`. Set `ROUTER_LABEL` to a name you want in Grafana. Edit `INTERFACE_NAMES_JSON` for friendly AP/path names and optionally `DEVICE_NAMES_JSON` for MAC-to-device names. Keep the outer single quotes around each JSON mapping; they preserve the JSON when systemd loads the file. The sample contains placeholders only.

`POLL_INTERVAL_SECONDS` controls the continuous service's poll interval. The default is 120 seconds. The Grafana URL must use HTTPS. This service sends protobuf + Snappy Prometheus remote write requests with Basic Auth, the same delivery approach as JK-BMS Cloud Bridge. It does not expose a local port.

### 6. Make one test push, then enable periodic collection

Install the units and start the one-shot service once:

```sh
sudo install -m 0644 /opt/mikrotik-client-monitor/systemd/mikrotik-client-monitor-once.service /etc/systemd/system/
sudo install -m 0644 /opt/mikrotik-client-monitor/systemd/mikrotik-client-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start mikrotik-client-monitor-once.service
sudo journalctl -u mikrotik-client-monitor-once.service -n 30 --no-pager
```

Look for `Collected ... clients` and `Pushed ... metric series`. Then enable continuous polling:

```sh
sudo systemctl enable --now mikrotik-client-monitor.service
sudo systemctl status mikrotik-client-monitor.service --no-pager
sudo journalctl -u mikrotik-client-monitor.service -n 30 --no-pager
```

In Grafana **Explore**, select your Prometheus data source and run `mikrotik_collector_up` as an instant query. Expect `1` for your `router` label. Then run `mikrotik_clients_by_interface` and `mikrotik_client_info`. Create the table and count panels using the queries below.

The supplied timer is an alternative to the continuous service. To use it, install `systemd/mikrotik-client-monitor.timer`, edit its `OnUnitActiveSec` schedule, reload systemd, and enable the timer. The timer's interval comes from that unit; `POLL_INTERVAL_SECONDS` applies only to continuous `run` mode. Run either the continuous service or the timer, not both.

### If setup fails

Check the one-shot journal first. SSH errors usually mean the router user, key, host key file, port, or RouterOS permissions need correction. HTTP `401` or `403` means the remote write URL, instance ID, or token/scope needs correction. If the push succeeds but no clients appear, inspect the two SSH command outputs: the bridge must learn MACs on the expected interfaces, and DHCP enrichment requires bound leases. An AP behind a shared switch cannot be distinguished from other devices on that same router-facing port.

On SSH collection failure the service attempts to push `mikrotik_collector_up=0`; on remote write failure it exits nonzero in `once` mode. It does not log credentials or HTTP response bodies.

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
