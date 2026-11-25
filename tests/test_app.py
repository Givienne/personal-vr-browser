import json
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class ParserTests(unittest.TestCase):
    def test_import_semicolon_and_optional_fields(self):
        sample = 'Game Name;Package Name;Version Code;Size (MB);Last Updated;Release Name;Downloads;Description\n"VR; Arcade";org.example.arcade;1.3;123;2026-11-20;Arcade v1;42;Great game\n'
        games, skipped = app.parse_gamelist(sample)
        self.assertEqual(skipped, 0)
        self.assertEqual(games[0]['name'], 'VR; Arcade')
        self.assertEqual(games[0]['version'], '1.3')
        self.assertEqual(games[0]['downloads'], 42)
        self.assertEqual(games[0]['description'], 'Great game')

    def test_rejects_unsafe_ids(self):
        txt = 'Game Name;Package Name\nGood;org.example.good\nBad;../../etc/passwd\nOther;org.example.good\n'
        games, skipped = app.parse_gamelist(txt)
        self.assertEqual(len(games), 1)
        self.assertEqual(skipped, 1)

    def test_requires_columns(self):
        with self.assertRaises(ValueError):
            app.parse_gamelist('Title;File\nFoo;bar\n')


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.data = Path(cls.temp.name)
        entries, _ = app.parse_gamelist('Game Name;Package Name;Version Code;Size (MB);Last Updated;Release Name;Downloads\nOrbital Test;org.example.orbital;0.1;1;2026-11-19;orbital-v01;6\nQuiet Room;org.example.quiet;0.3;2;2026-11-17;quiet-v03;2\n')
        app.write_catalog(entries, cls.data)
        root = cls.data / 'packages' / 'org.example.orbital'
        root.mkdir(parents=True)
        (root / 'orbital.apk').write_bytes(b'0123456789')
        (root / 'not-public.txt').write_text('never download')
        (root / 'leak.apk').symlink_to('/etc/hosts')
        cls.server = app.LibraryServer(('127.0.0.1', 0), app.Handler, cls.data, False)
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp.cleanup()

    def request(self, path, headers=None, method='GET'):
        try:
            with urlopen(Request(self.url+path,headers=headers or {},method=method),timeout=5) as r:
                return r.status,r.headers,r.read()
        except HTTPError as e:
            return e.code,e.headers,e.read()

    def test_status_when_private_config_is_unreadable(self):
        from unittest.mock import patch
        with patch.object(app.vrsrc_source, 'SOURCE_FILE') as protected, \
             patch.object(app.vrsrc_source, 'provider_access_enabled', return_value=False):
            protected.is_file.side_effect = PermissionError("config is private")
            status, _, body = self.request('/api/status')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data['games'], 2)
        self.assertEqual(data['source']['state'], 'authorization_required')

    def test_site_and_status(self):
        status,headers,content=self.request('/')
        self.assertEqual(status,200)
        self.assertIn(b'Vialenne VR', content)
        self.assertIn("script-src 'self'",headers['Content-Security-Policy'])
        status,_,content=self.request('/api/status')
        self.assertEqual(json.loads(content)['games'],2)

    def test_filters_and_search(self):
        status,_,body=self.request('/api/games?q=orbital')
        found=json.loads(body)
        self.assertEqual(status,200)
        self.assertEqual(found['total'],1)
        self.assertTrue(found['items'][0]['hasFiles'])
        _,_,body=self.request('/api/games?filter=downloads')
        self.assertEqual(json.loads(body)['total'],1)
        _,_,body=self.request('/api/games?sort=updated&order=desc')
        self.assertEqual(json.loads(body)['items'][0]['name'],'Orbital Test')

    def test_favorites_filter(self):
        _,_,body=self.request('/api/games?filter=favorites&favorites=org.example.quiet')
        found=json.loads(body)
        self.assertEqual(found['total'],1)
        self.assertEqual(found['items'][0]['id'],'org.example.quiet')

    def test_file_download_range_and_head(self):
        path='/api/download/org.example.orbital/orbital.apk'
        status,headers,body=self.request(path,{'Range':'bytes=2-5'})
        self.assertEqual(status,206)
        self.assertEqual(body,b'2345')
        self.assertEqual(headers['Content-Range'],'bytes 2-5/10')
        status,headers,body=self.request(path,method='HEAD')
        self.assertEqual(status,200)
        self.assertEqual(headers['Content-Length'],'10')
        self.assertEqual(body,b'')
        status,_,body=self.request(path,{'Range':'bytes=200-300'})
        self.assertEqual(status,416)

    def test_downloads_block_unknown_and_symlinks(self):
        for path in ('/api/download/org.example.orbital/not-public.txt',
                     '/api/download/org.example.orbital/leak.apk',
                     '/api/download/org.example.unknown/hack.apk',
                     '/api/download/org.example.orbital/..%2F..%2Fetc%2Fpasswd'):
            status,_,_=self.request(path)
            self.assertEqual(status,404,path)


    def test_public_mirror_relay_removed(self):
        for path in ('/api/remote/org.example.orbital/game.apk',
                     '/api/remote/org.example.orbital/anything.7z'):
            status,_,_=self.request(path)
            self.assertEqual(status,404,path)

if __name__=='__main__':
    unittest.main(verbosity=2)
