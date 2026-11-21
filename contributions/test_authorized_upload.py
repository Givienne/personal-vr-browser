import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from authorized_upload import chosen_file, destination_remote, private_config, send_one
from contribution_queue import add_candidates, load_queue, save_queue, sha256
from prepare_submission import export


class TestUploadAdapter(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.queue = self.root / "queue.json"
        self.file = self.root / "owned.apk"
        self.file.write_bytes(b"fixture not an APK")
        self.config = self.root / "upload.config"
        self.config.write_text("[approvedremote]\ntype = webdav\nurl = https://example.invalid\n")
        self.config.chmod(0o600)
        self.receipt = self.root / "receipt.json"
        self.destination = "approvedremote:some/path/owned.apk"
        add_candidates(self.queue, [{"package":"org.example.app", "installed":42, "catalog":41}])

    def approve(self):
        items = load_queue(self.queue)
        items[0].update(status="approved", file=str(self.file), sha256=sha256(self.file))
        save_queue(self.queue, items)

    def send(self, **kw):
        fields = dict(queue=self.queue, package="org.example.app", version=42,
                      upload_config=self.config, destination=self.destination,
                      receipt=self.receipt, acknowledged=True,
                      environ={"VRSRC_ACCESS_APPROVED":"1", "VRSRC_UPLOAD_AUTHORIZED":"1"})
        fields.update(kw)
        return send_one(**fields)

    def test_local_plan_no_upload(self):
        output = self.root / "plan.json"
        with patch("authorized_upload.subprocess.run") as run:
            self.assertEqual(export(self.queue, output), 0)
            run.assert_not_called()
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertFalse(json.loads(output.read_text())["providerSubmissionEnabled"])

    def test_verified_selection(self):
        self.approve()
        self.assertEqual(chosen_file(self.queue, "org.example.app", 42)["sha256"], sha256(self.file))

    def test_pending_cannot_send(self):
        with patch("authorized_upload.subprocess.run") as run, patch("authorized_upload.shutil.which", return_value="/usr/bin/rclone"):
            with self.assertRaisesRegex(ValueError, "No unique, approved"):
                self.send()
            run.assert_not_called()

    def test_requires_both_authorization_gates(self):
        self.approve()
        with patch("authorized_upload.subprocess.run") as run:
            for env in ({}, {"VRSRC_ACCESS_APPROVED":"1"}, {"VRSRC_UPLOAD_AUTHORIZED":"1"}):
                with self.assertRaisesRegex(ValueError, "authorization"):
                    self.send(environ=env)
            run.assert_not_called()

    def test_requires_acknowledgement(self):
        self.approve()
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            self.send(acknowledged=False)

    def test_private_config(self):
        self.assertEqual(private_config(self.config, "approvedremote"), self.config)
        self.config.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "group or others"):
            private_config(self.config, "approvedremote")

    def test_reject_missing_remote_and_local(self):
        with self.assertRaisesRegex(ValueError, "not defined"):
            private_config(self.config, "unknown")
        self.config.write_text("[approvedremote]\ntype = local\n")
        with self.assertRaisesRegex(ValueError, "non-local"):
            private_config(self.config, "approvedremote")

    def test_destination_safety(self):
        for target in ("/tmp/file.apk", "approvedremote:/absolute", "approvedremote:../file.apk",
                       "approvedremote:thing/../file.apk", "approvedremote:", "approvedremote:foo\\bar"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                destination_remote(target)
        self.assertEqual(destination_remote(self.destination), "approvedremote")

    def test_changed_hash_cannot_send(self):
        self.approve()
        self.file.write_bytes(b"mutated")
        with patch("authorized_upload.subprocess.run") as run, patch("authorized_upload.shutil.which", return_value="/usr/bin/rclone"):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.send()
            run.assert_not_called()

    def test_one_upload_with_explicit_destination_and_receipt(self):
        self.approve()
        class Result:
            returncode = 0
        with patch("authorized_upload.subprocess.run", return_value=Result()) as run, \
             patch("authorized_upload.shutil.which", return_value="/usr/bin/rclone"):
            self.send()
            run.assert_called_once()
            argv = run.call_args.args[0]
            self.assertEqual(argv[3], "copyto")
            self.assertEqual(argv[5], self.destination)
            self.assertIn("--tpslimit", argv)
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(receipt["versionCode"], 42)
        self.assertNotIn("url", receipt)
        self.assertEqual(self.receipt.stat().st_mode & 0o777, 0o600)

    def test_existing_receipt_refuses_new_attempt(self):
        self.approve()
        self.receipt.write_text("already attempted")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.send()

    def test_failed_upload_does_not_claim_success(self):
        self.approve()
        class Result:
            returncode = 1
        with patch("authorized_upload.subprocess.run", return_value=Result()), \
             patch("authorized_upload.shutil.which", return_value="/usr/bin/rclone"):
            with self.assertRaisesRegex(RuntimeError, "partial transfer"):
                self.send()
        self.assertFalse(self.receipt.exists())


if __name__ == "__main__":
    unittest.main()
