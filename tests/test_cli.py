import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mikrotik_client_monitor.cli import Config, cycle
from mikrotik_client_monitor.collector import CollectionError


CONFIG = Config("router.example.net", 22, "metrics-reader", "/path/key",
                "/path/known_hosts", 15, 120, "router-a", {}, {},
                "https://metrics.example.net/api/prom/push", "user", "token", 15)


class CLITests(unittest.TestCase):
    @patch("mikrotik_client_monitor.cli.push_request")
    @patch("mikrotik_client_monitor.cli.collect", side_effect=CollectionError("SSH failed"))
    def test_router_failure_pushes_health_zero(self, _collect, push):
        ok, last_success = cycle(CONFIG)
        self.assertFalse(ok)
        self.assertEqual(last_success, 0)
        request = push.call_args.args[0]
        self.assertEqual(len(request.timeseries), 1)
        self.assertEqual(request.timeseries[0].samples[0].value, 0)


if __name__ == "__main__":
    unittest.main()
