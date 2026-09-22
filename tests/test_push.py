import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mikrotik_client_monitor.collector import Client, Snapshot
from mikrotik_client_monitor.push import build_request, push_request


class PushTests(unittest.TestCase):
    def test_request_contains_location_and_health(self):
        client = Client("02:00:00:00:00:01", "198.51.100.10", "Sensor",
                        "ether2", "Office AP", "bridge", "")
        snapshot = Snapshot((client,), {("bridge", "ether2", "Office AP"): 1},
                            1, 1, 0, 0)
        request = build_request(snapshot, "router-a", 1234)
        indexed = {next(label.value for label in series.labels if label.name == "__name__"): series
                   for series in request.timeseries}
        self.assertEqual(indexed["mikrotik_collector_up"].samples[0].value, 1)
        labels = {label.name: label.value for label in indexed["mikrotik_client_info"].labels}
        self.assertEqual(labels["ap"], "Office AP")
        self.assertEqual(labels["ip"], "198.51.100.10")

    @patch("mikrotik_client_monitor.push.requests.post")
    def test_remote_write_headers_and_auth(self, post):
        post.return_value.status_code = 204
        request = build_request(None, "router-a", 0)
        push_request(request, "https://metrics.example.net/api/prom/push", "user", "token", 15)
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["auth"], ("user", "token"))
        self.assertEqual(kwargs["headers"]["Content-Encoding"], "snappy")
        self.assertTrue(kwargs["data"])


if __name__ == "__main__":
    unittest.main()
