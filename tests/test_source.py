from pathlib import Path
import base64
import json
import tempfile
import unittest
from unittest.mock import patch
import vrsrc_source as vs


class SingleSourceTests(unittest.TestCase):
    def test_fixed_config_and_secret_never_returned_in_status(self):
        with tempfile.TemporaryDirectory() as td:
            cfg=Path(td)/'ServerInfo.json';data=Path(td)/'data';data.mkdir()
            cfg.write_text(json.dumps({'baseUri':'https://example.invalid/store/', 'password':base64.b64encode(b'secret').decode()}))
            with patch.object(vs, 'SOURCE_FILE', cfg):
                src=vs.read_source()
                self.assertEqual(src['baseUri'],'https://example.invalid/store/')
                self.assertEqual(vs.decoded_archive_password(src),'secret')
                self.assertFalse(src['allowDownloads'])
                status=vs.source_state(data)
                self.assertNotIn('password',json.dumps(status))
                self.assertEqual(status['state'],'authorization_required')

    def test_https_only_and_safe_file_names(self):
        with tempfile.TemporaryDirectory() as td:
            cfg=Path(td)/'ServerInfo.json'
            cfg.write_text(json.dumps({'baseUri':'http://localhost:3939/', 'password':'aGVsbG8='}))
            with patch.object(vs,'SOURCE_FILE',cfg):
                with self.assertRaises(ValueError):vs.read_source()
            base={'baseUri':'https://example.invalid/','allowDownloads':True}
            self.assertRegex(vs.source_hash('Sample VR'),'^[a-f0-9]{32}$')
            with patch.dict('os.environ', {'VRSRC_ACCESS_APPROVED':'1', 'VRSRC_API_KEY':'test-key'}):
                self.assertTrue(vs.remote_url(base,'Sample VR','game.7z.001').startswith('https://example.invalid/'))
            with self.assertRaises(ValueError):vs.remote_url(base,'Sample VR','../../etc/passwd')
            with self.assertRaises(ValueError):vs.remote_url(base,'Sample VR','folder/abc.apk')

    def test_default_unconfigured(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(vs,'SOURCE_FILE',Path(td)/'does-not-exist'):
                self.assertIsNone(vs.read_source())
                self.assertEqual(vs.source_state(Path(td))['state'],'unconfigured')

    def test_automatic_sync_recognizes_original_case_and_preserves_source_secret(self):
        import subprocess
        from unittest.mock import patch
        from app import parse_gamelist, write_catalog
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); data=root/'data'; data.mkdir()
            cfg=root/'ServerInfo.json'
            cfg.write_text(json.dumps({'baseUri':'https://example.invalid/', 'password':base64.b64encode(b'secret').decode()}))
            def fake_run(cmd, **kwargs):
                if cmd[1]=='copyto':
                    (data/next(p.name for p in data.iterdir() if p.name.startswith('vrsrc-'))/'meta.7z').write_bytes(b'fake archive')
                elif cmd[1]=='x':
                    dest=next(a[2:] for a in cmd if a.startswith('-o'))
                    Path(dest,'VRP-GameList.txt').write_text('Game Name;Package Name;Version Code;Size (MB);Last Updated;Release Name;Downloads\nExample;com.example.fake;1;100;2026-11-20;Example Release;2\n')
                    self.assertEqual(kwargs.get('input'), 'secret\n')
                    self.assertNotIn('secret', str(cmd))
                return subprocess.CompletedProcess(cmd, 0, '', '')
            with patch.object(vs,'SOURCE_FILE',cfg), patch.dict('os.environ', {'VRSRC_ACCESS_APPROVED':'1', 'VRSRC_API_KEY':'dummy-test-key'}), patch.object(vs.shutil,'which',side_effect=lambda c:'/fake/bin/'+c), patch.object(vs.subprocess,'run',side_effect=fake_run):
                self.assertTrue(vs.sync_metadata(data,parse_gamelist,write_catalog))
            rows=json.loads((data/'catalog.json').read_text())
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['name'],'Example')
            self.assertEqual(json.loads((data/'source-status.json').read_text())['state'],'ready')


if __name__=='__main__':unittest.main()
