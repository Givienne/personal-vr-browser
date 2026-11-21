import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import vrsrc_source as source


class ProviderAccessTests(unittest.TestCase):
    def test_sync_requires_approved_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'ServerInfo.json'
            config.write_text(json.dumps({'baseUri': 'https://example.invalid/', 'password': 'cGFzcw=='}))
            data = Path(tmp) / 'data'
            with patch.object(source, 'SOURCE_FILE', config), \
                 patch.dict('os.environ', {'VRSRC_API_KEY': '', 'VRSRC_ACCESS_APPROVED': ''}), \
                 patch.object(source.subprocess, 'run') as run:
                self.assertFalse(source.provider_access_enabled())
                self.assertFalse(source.sync_metadata(data, lambda _: ([], 0), lambda *_: None))
                self.assertEqual(source.source_state(data)['state'], 'authorization_required')
                run.assert_not_called()

    def test_listing_cache_and_spacing(self):
        config = {'baseUri': 'https://example.invalid/', 'allowDownloads': True}
        sample = json.dumps([{'Name': 'part.7z', 'Size': 123, 'IsDir': False}])
        with patch.dict('os.environ', {'VRSRC_API_KEY': 'test-only', 'VRSRC_ACCESS_APPROVED': '1'}), \
             patch.object(source, '_remote_cache', {}), \
             patch.object(source, '_next_remote_lookup', 0.0), \
             patch.object(source.shutil, 'which', return_value='/usr/bin/rclone'), \
             patch.object(source.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, sample, '')) as run:
            first = source.list_remote_files(config, 'Example v1', 'org.example.app')
            second = source.list_remote_files(config, 'Example v1', 'org.example.app')
            limited = source.list_remote_files(config, 'Another v2', 'org.example.other')
            self.assertEqual(first, second)
            self.assertEqual([row['name'] for row in first], ['part.7z'])
            self.assertEqual(limited, [])
            self.assertEqual(run.call_count, 1)
            self.assertIn('X-API-Key: test-only', run.call_args.kwargs['env']['RCLONE_HEADER'])


if __name__ == '__main__':
    unittest.main()
