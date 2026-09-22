import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mikrotik_client_monitor.collector import CollectionError, make_snapshot, parse_detail


HOSTS = '''Flags: D - DYNAMIC; L - LOCAL; E - EXTERNAL
 0 D E mac-address=02:00:00:00:00:01 on-interface=ether2 bridge=bridge
 1 D E mac-address=02:00:00:00:00:02 on-interface=ether3 bridge=bridge vid=20
 2 DL mac-address=02:00:00:00:00:03 on-interface=bridge bridge=bridge
 3 D E mac-address=02:00:00:00:00:04 on-interface=ether2 bridge=bridge
 4 D E mac-address=02:00:00:00:00:04 on-interface=ether3 bridge=bridge
'''
LEASES = ''' 0 D address=198.51.100.10 mac-address=02:00:00:00:00:01 status=bound
     active-address=198.51.100.10 active-mac-address=02:00:00:00:00:01
     host-name="Sensor \\"A\\""
 1 D address=198.51.100.11 mac-address=02:00:00:00:00:09 status=bound
     active-address=198.51.100.11 active-mac-address=02:00:00:00:00:09
 2 D address=198.51.100.12 mac-address=02:00:00:00:00:02 status=waiting
'''


class CollectorTests(unittest.TestCase):
    def test_join_and_group(self):
        snap = make_snapshot(HOSTS, LEASES, {"ether2": "Office AP"},
                             {"02:00:00:00:00:01": "Desk sensor"})
        self.assertEqual(len(snap.clients), 2)
        self.assertEqual(snap.clients[0].hostname, "Desk sensor")
        self.assertEqual(snap.clients[0].ip, "198.51.100.10")
        self.assertEqual(snap.clients[0].ap, "Office AP")
        self.assertEqual(snap.clients[1].ip, "")
        self.assertEqual(snap.counts[("bridge", "ether3", "ether3")], 1)
        self.assertEqual(snap.ambiguous_macs, 1)
        self.assertEqual(snap.unmatched_leases, 1)

    def test_quoted_and_wrapped(self):
        rows = parse_detail(LEASES)
        self.assertEqual(rows[0][0]["host-name"], 'Sensor "A"')
        self.assertEqual(rows[0][0]["status"], "bound")

    def test_malformed_output_fails_closed(self):
        with self.assertRaises(CollectionError):
            parse_detail("failure: not enough permissions")
        with self.assertRaises(CollectionError):
            parse_detail(' 0 D mac-address="broken')
        with self.assertRaises(CollectionError):
            parse_detail("unexpected text")


if __name__ == "__main__":
    unittest.main()
