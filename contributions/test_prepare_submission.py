import json
import tempfile
import unittest
from pathlib import Path
from contribution_queue import add_candidates, load_queue, save_queue, sha256
from prepare_submission import preflight, export

class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.root = Path(self.t.name)
        self.q = self.root / 'queue.json'
        add_candidates(self.q, [{'package':'org.example.test','installed':5,'catalog':4,'reason':'newer version'}])
        self.f = self.root / 'myfile.apk'
        self.f.write_bytes(b'test fixture, not a real apk')

    def approve(self):
        items = load_queue(self.q)
        items[0].update(status='approved', file=str(self.f), sha256=sha256(self.f))
        save_queue(self.q,items)

    def test_pending_not_exported(self):
        self.assertEqual(preflight(load_queue(self.q)), [])

    def test_approved_export(self):
        self.approve()
        output = self.root / 'review.json'
        self.assertEqual(export(self.q, output), 1)
        obj = json.loads(output.read_text())
        self.assertEqual(obj['approved'][0]['versionCode'],5)
        self.assertFalse(obj['providerSubmissionEnabled'])

    def test_mutated_attachment_fails(self):
        self.approve()
        self.f.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'changed'):
            preflight(load_queue(self.q))

    def test_output_cannot_overwrite(self):
        self.approve()
        output = self.root / 'review.json'
        output.write_text('do not overwrite')
        with self.assertRaisesRegex(ValueError,'exists'):
            export(self.q, output)
        self.assertEqual(output.read_text(),'do not overwrite')

    def test_symlink_rejected(self):
        self.approve()
        lnk = self.root / 'linked.apk'
        lnk.symlink_to(self.f)
        items = load_queue(self.q)
        items[0]['file'] = str(lnk)
        save_queue(self.q, items)
        with self.assertRaisesRegex(ValueError,'linked'):
            preflight(load_queue(self.q))

if __name__ == '__main__':
    unittest.main()
