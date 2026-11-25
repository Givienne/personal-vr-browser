import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from authorized_upload import main
from prepare_submission import export

class OfflineOnlyTests(unittest.TestCase):
    def test_plan_has_no_provider_upload_and_no_send(self):
        with tempfile.TemporaryDirectory() as d:
            directory=Path(d)
            queue=directory/'queue.json';queue.write_text('[]')
            output=directory/'plan.json'
            with patch('sys.argv',['authorized_upload.py','plan','--queue',str(queue),'--output',str(output)]),\
                 patch('subprocess.run') as run:
                main()
            run.assert_not_called()
            data=json.loads(output.read_text())
            self.assertEqual(data['approved'],[])
            self.assertFalse(data['providerSubmissionEnabled'])
    def test_arbitrary_send_cli_removed(self):
        with patch('sys.argv',['authorized_upload.py','send','--destination','anything:anywhere']),\
             self.assertRaises(SystemExit):
            main()

if __name__=='__main__':unittest.main()
