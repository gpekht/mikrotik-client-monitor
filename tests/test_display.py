import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mikrotik_client_monitor.cli import config_from_env
from mikrotik_client_monitor.collector import Client, Snapshot
from mikrotik_client_monitor.display import render_snapshot


class DisplayTests(unittest.TestCase):
    def test_groups_clients_and_escapes_control_characters(self):
        clients = (
            Client("02:00:00:00:00:01", "198.51.100.10", "Sensor\x1b[31m",
                   "ether2", "Office AP", "bridge", ""),
            Client("02:00:00:00:00:02", "", "",
                   "ether3", "Workshop", "bridge", "20"),
        )
        snapshot = Snapshot(clients, {("bridge", "ether2", "Office AP"): 1,
                                      ("bridge", "ether3", "Workshop"): 1},
                            2, 1, 0, 0)
        output = render_snapshot(snapshot)
        self.assertIn("=== Office AP (ether2)", output)
        self.assertIn("=== Workshop (ether3)", output)
        self.assertIn("198.51.100.10", output)
        self.assertIn("<unknown>", output)
        self.assertNotIn("\x1b", output)

    def test_show_configuration_does_not_need_cloud_credentials(self):
        values = {
            "ROUTER_HOST": "router.example.net", "ROUTER_USER": "metrics-reader",
            "SSH_KEY_PATH": "/path/key", "SSH_KNOWN_HOSTS": "/path/known_hosts",
        }
        with patch.dict(os.environ, values, clear=True):
            config = config_from_env(require_push=False)
        self.assertEqual(config.grafana_url, "")
        self.assertEqual(config.grafana_token, "")


if __name__ == "__main__":
    unittest.main()
