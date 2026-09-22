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

## View clients immediately in the Pi terminal

After setup, log in to the Pi and run:

```sh
cd "$HOME/mikrotik-client-monitor"
venv/bin/mikrotik-client-monitor show
```

This makes a fresh SSH read and prints each learned client under its router-facing interface, with IP, MAC, VLAN, and DHCP or configured name. Clients without a bound DHCP lease show `-` for IP and `<unknown>` for name. Friendly group names come from `INTERFACE_NAMES_JSON` in your private `.env`; otherwise the RouterOS interface name is shown. `show` does not push metrics or change the timer. It only needs the router and SSH settings, so Grafana credentials are optional for this command. Run it again whenever you want a fresh view.

## Raspberry Pi / Linux setup in your home folder

This follows the JK-BMS layout: a checkout at `~/mikrotik-client-monitor`, a `venv/` inside it, and a private `.env` beside the code. A one-shot user-level systemd timer runs the collector. The SSH key and pinned router host key live in `~/.ssh`; unit files live in `~/.config/systemd/user`. No project files or secrets need to be installed under `/opt` or `/etc`.

### 1. Clone and install

Run these as the Linux user who will own the service (not root):

```sh
sudo apt update
sudo apt install -y git python3 python3-venv openssh-client
git clone https://github.com/gpekht/mikrotik-client-monitor.git "$HOME/mikrotik-client-monitor"
cd "$HOME/mikrotik-client-monitor"
python3 -m venv venv
venv/bin/pip install .
```

### 2. Generate a dedicated SSH key

```sh
install -d -m 0700 "$HOME/.ssh"
ssh-keygen -q -t ed25519 -N '' -f "$HOME/.ssh/mikrotik_monitor_ed25519"
cat "$HOME/.ssh/mikrotik_monitor_ed25519.pub"
```

Copy the **public** key printed by the last command, or transfer only the `.pub` file to your administrator workstation. The private key is the file without `.pub`; keep it on the Linux host and out of Git. The key has no passphrase so the timer can run unattended; keep `~/.ssh` private.

### 3. Install the public key on the MikroTik

In an administrative RouterOS terminal, create a custom group with only `ssh,read` policies and a collector user:

```routeros
/user group add name=metrics-read policy=ssh,read
/user add name=metrics-reader group=metrics-read password="<unique-strong-password>"
```

Paste the complete `ssh-ed25519 ...` public key from step 2:

```routeros
/user ssh-keys add user=metrics-reader key="<paste-the-public-ssh-key>"
```

Alternatively, save the public key as `mikrotik-monitor.pub` on your workstation, upload that `.pub` file through WinBox **Files**, then run:

```routeros
/user ssh-keys import public-key-file=mikrotik-monitor.pub user=metrics-reader
```

Only one of these methods is needed. **Never upload the private key.** The built-in RouterOS `read` group grants more permissions than this collector needs. Limit SSH access to the Linux host in the router firewall or `/ip service` rules. See MikroTik's [SSH key instructions](https://manual.mikrotik.com/docs/authentication-authorization-accounting/user/) and [WinBox file transfer instructions](https://manual.mikrotik.com/docs/management-tools/winbox-legacy/).

### 4. Pin the router host key and test SSH

Set these shell variables on the Linux host, replacing the example hostname and port:

```sh
ROUTER_ADDRESS=router.example.net
ROUTER_SSH_PORT=22
ssh-keyscan -p "$ROUTER_SSH_PORT" "$ROUTER_ADDRESS" > "$HOME/.ssh/mikrotik_monitor_known_hosts.candidate"
ssh-keygen -lf "$HOME/.ssh/mikrotik_monitor_known_hosts.candidate"
```

Compare the fingerprint with the router's host key through a separately trusted administrative connection. Once verified, install the candidate locally and test both read commands:

```sh
mv "$HOME/.ssh/mikrotik_monitor_known_hosts.candidate" "$HOME/.ssh/mikrotik_monitor_known_hosts"
chmod 600 "$HOME/.ssh/mikrotik_monitor_known_hosts"
ssh -T -p "$ROUTER_SSH_PORT" -i "$HOME/.ssh/mikrotik_monitor_ed25519" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/mikrotik_monitor_known_hosts" "metrics-reader@$ROUTER_ADDRESS" '/interface bridge host print detail without-paging proplist=mac-address,on-interface,bridge,vid,local,invalid,disabled'
ssh -T -p "$ROUTER_SSH_PORT" -i "$HOME/.ssh/mikrotik_monitor_ed25519" -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$HOME/.ssh/mikrotik_monitor_known_hosts" "metrics-reader@$ROUTER_ADDRESS" '/ip dhcp-server lease print detail without-paging proplist=status,active-mac-address,active-address,mac-address,address,host-name'
```

Both should return RouterOS property rows without a password prompt. The collector rejects an unknown or changed host key.

### 5. Configure Grafana Cloud and run once

In Grafana Cloud, open your stack's **Details → Prometheus → Details** and copy the remote write URL and metrics instance ID. Create a Cloud Access Policy token with `metrics:write` scope, or use an existing token scoped for writing metrics. Then, from the checkout:

```sh
cp .env.example .env
chmod 600 .env
nano .env
```

Replace `ROUTER_HOST`, `ROUTER_PORT`, `ROUTER_USER`, the `YOUR_USER` placeholders in `SSH_KEY_PATH` and `SSH_KNOWN_HOSTS`, `GRAFANA_URL`, `GRAFANA_USER`, and `GRAFANA_TOKEN`. Set `ROUTER_LABEL` to a useful name and edit `INTERFACE_NAMES_JSON` for friendly AP/path names. Optionally use `DEVICE_NAMES_JSON` for MAC-to-device names. Keep the outer single quotes around JSON mappings so both the local loader and systemd preserve the JSON. The sample has placeholders only; `.env` is ignored by Git.

```sh
venv/bin/mikrotik-client-monitor once
```

Expect `Collected ... clients` and `Pushed ... metric series`. The remote write URL must use HTTPS. This is the same protobuf + Snappy remote write approach as JK-BMS Cloud Bridge; no local listener is needed.

### 6. Run it on a user-level systemd timer

Install the included units in your home directory and test the one-shot service:

```sh
mkdir -p "$HOME/.config/systemd/user"
cp systemd/mikrotik-client-monitor.service systemd/mikrotik-client-monitor.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user start mikrotik-client-monitor.service
journalctl --user -u mikrotik-client-monitor.service -n 30 --no-pager
```

The timer runs one minute after the user manager starts and then two minutes after each run finishes, like the JK-BMS one-shot timer. To change the cadence, edit `OnUnitInactiveSec` in `~/.config/systemd/user/mikrotik-client-monitor.timer` before enabling it, then run `systemctl --user daemon-reload`. Enable the timer and permit user services to start at boot even when you are logged out:

```sh
systemctl --user enable --now mikrotik-client-monitor.timer
sudo loginctl enable-linger "$USER"
systemctl --user list-timers mikrotik-client-monitor.timer
```

`POLL_INTERVAL_SECONDS` in `.env` applies only if you run the optional continuous `venv/bin/mikrotik-client-monitor run` command; the systemd timer cadence is set in its timer unit. Use the timer for the JK-BMS-style deployment.

In Grafana **Explore**, select your Prometheus data source and run `mikrotik_collector_up` as an instant query. Expect `1` for your `router` label. Then run `mikrotik_clients_by_interface` and `mikrotik_client_info`, and create panels using the queries below.

### If setup fails

Check `journalctl --user -u mikrotik-client-monitor.service -n 50 --no-pager`. SSH errors usually mean the router user, key, host key file, port, or RouterOS permissions need correction. HTTP `401` or `403` means the remote write URL, instance ID, or token/scope needs correction. If the push succeeds but no clients appear, inspect the two SSH command outputs: the bridge must learn MACs on the expected interfaces, and DHCP enrichment requires bound leases. An AP behind a shared switch cannot be distinguished from other devices on that same router-facing port.

On SSH collection failure the service attempts to push `mikrotik_collector_up=0`; on remote write failure the one-shot command exits nonzero. It does not log credentials or HTTP response bodies.

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
