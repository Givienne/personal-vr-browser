import json
import tempfile
import unittest
from pathlib import Path
from contribution_queue import add_candidates, load_queue, sha256

class TestContributionQueue(unittest.TestCase):
    def test_import_dedup_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/"queue.json"
            candidates = [{"package":"org.example.game", "installed":42, "catalog":41, "reason":"newer version"}]
            self.assertEqual(add_candidates(p, candidates), 1)
            self.assertEqual(add_candidates(p, candidates), 0)
            self.assertEqual(load_queue(p)[0]["status"], "pending")
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
    def test_invalid_package(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                add_candidates(Path(temp)/"queue.json", [{"package":"../bad", "installed":1}])
    def test_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)/"file.apk"
            p.write_bytes(b"test")
            self.assertEqual(len(sha256(p)), 64)

if __name__ == "__main__":
    unittest.main()
