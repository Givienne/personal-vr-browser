import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import vrsrc_source as src

class MockResponse(io.BytesIO):
    def __init__(self, status=200, headers=None, data=b'fake file', url='https://go.srcdl1.xyz/meta.7z'):
        super().__init__(data)
        self.status=status
        self.headers=headers or {}
        self._url=url
    def geturl(self):return self._url

class MockKeys:
    def __init__(self):self.count=0;self.invalidated=0
    def get(self): self.count+=1;return 'temporary-key'
    def invalidate(self, forbidden=False):self.invalidated+=1

class MockOpener:
    def __init__(self, replies):self.replies=list(replies);self.requests=[]
    def open(self, req, timeout=0):
        self.requests.append(req)
        response=self.replies.pop(0)
        if isinstance(response,Exception):raise response
        return response

class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.data=Path(self.temp.name)
        self.config=self.data/'server.json'

    def test_inaccessible_private_config_does_not_break_status(self):
        from unittest.mock import Mock
        protected = Mock()
        protected.is_file.side_effect = PermissionError("config is private")
        with patch.object(src, 'SOURCE_FILE', protected), \
             patch.object(src, 'provider_access_enabled', return_value=False):
            self.assertEqual(src.source_state(self.data),
                             {"state": "authorization_required", "name": "VRSRC"})
        with patch.object(src, 'SOURCE_FILE', protected), \
             patch.object(src, 'provider_access_enabled', return_value=True):
            self.assertEqual(src.source_state(self.data),
                             {"state": "error", "name": "VRSRC"})

    def test_disabled_sync_does_not_read_private_config(self):
        with patch.object(src, 'provider_access_enabled', return_value=False), \
             patch.object(src, 'read_source') as config:
            self.assertFalse(src.sync_metadata(self.data, lambda _: ([],0), lambda *_:None))
            config.assert_not_called()

    def test_only_exact_mirror_origin_accepted(self):
        bad=('https://elsewhere.invalid','https://go.srcdl1.xyz.evil.invalid',
             'https://go.srcdl1.xyz:1234','http://go.srcdl1.xyz',
             'https://go.srcdl1.xyz@evil.invalid','https://go.srcdl1.xyz/elsewhere')
        for base in bad:
            self.config.write_text(json.dumps({'baseUri':base}))
            with patch.object(src,'SOURCE_FILE',self.config),self.assertRaises(ValueError):
                src.read_source()
        self.config.write_text(json.dumps({'baseUri':'https://go.srcdl1.xyz'}))
        with patch.object(src,'SOURCE_FILE',self.config):
            self.assertEqual(src.read_source()['baseUri'],'https://go.srcdl1.xyz/')

    def test_no_public_mirror_api(self):
        self.assertFalse(hasattr(src,'remote_url'))
        self.assertFalse(hasattr(src,'list_remote_files'))

    def test_redirect_rejected(self):
        self.assertIsNone(src.NoRedirects().redirect_request(None,None,301,'Move',{},'https://hostile.invalid'))

    def test_reject_unrelated_resources(self):
        with patch.object(src,'provider_access_enabled',return_value=True):
            with self.assertRaises(ValueError):src._mirror_request('GET','https://hostile.invalid/')

    def test_403_retries_once_with_new_key(self):
        keys=MockKeys()
        err=HTTPError('https://go.srcdl1.xyz/meta.7z',403,'Forbidden',{},io.BytesIO(b''))
        opener=MockOpener([err,MockResponse()])
        with patch.object(src,'provider_access_enabled',return_value=True),\
             patch.object(src,'runtime_keys',return_value=keys),\
             patch.object(src,'_opener',opener):
            response=src._mirror_request('HEAD','/meta.7z')
            self.assertEqual(response.status,200)
        self.assertEqual(keys.invalidated,1)
        self.assertEqual(len(opener.requests),2)
        for req in opener.requests:
            self.assertEqual(req.full_url,'https://go.srcdl1.xyz/meta.7z')
            self.assertEqual(req.get_header('X-api-key'),'temporary-key')

    def test_unchanged_etag_skips_get(self):
        (self.data/'meta.7z').write_bytes(b'cached')
        (self.data/'catalog.json').write_text('[]')
        src._write_probe(self.data,{'etag':'"A"','checkedAt':0,'downloadedAt':1})
        self.config.write_text(json.dumps({'baseUri':'https://go.srcdl1.xyz'}))
        opener=MockOpener([MockResponse(headers={'ETag':'"A"'})])
        with patch.object(src,'SOURCE_FILE',self.config),\
             patch.object(src,'provider_access_enabled',return_value=True),\
             patch.object(src,'runtime_keys',return_value=MockKeys()),\
             patch.object(src,'_opener',opener):
            self.assertTrue(src.sync_metadata(self.data,lambda _: ([],0),lambda *_:None))
        self.assertEqual([r.get_method() for r in opener.requests],['HEAD'])

    def test_new_data_downloads_once_then_caches(self):
        self.config.write_text(json.dumps({'baseUri':'https://go.srcdl1.xyz', 'password':''}))
        opener=MockOpener([MockResponse(headers={'ETag':'"NEW"'}),
                           MockResponse(headers={'ETag':'"NEW"'},data=b'filebytes')])
        def fake_extract(args,**kwargs):
            out=next(v[2:] for v in args if v.startswith('-o'))
            Path(out,'VRP-GameList.txt').write_text('sample')
        with patch.object(src,'SOURCE_FILE',self.config),\
             patch.object(src,'provider_access_enabled',return_value=True),\
             patch.object(src,'runtime_keys',return_value=MockKeys()),\
             patch.object(src,'_opener',opener),\
             patch.object(src.shutil,'which',return_value='/usr/bin/7z'),\
             patch.object(src.subprocess,'run',side_effect=fake_extract):
            def save(entries,dir): (dir/'catalog.json').write_text(json.dumps(entries))
            self.assertTrue(src.sync_metadata(self.data,lambda _: ([{'id':'org.example.app'}],0),save))
            self.assertTrue(src.sync_metadata(self.data,lambda _: ([],0),save))
        self.assertEqual([r.get_method() for r in opener.requests],['HEAD','GET'])
        self.assertEqual((self.data/'meta.7z').read_bytes(),b'filebytes')
        self.assertEqual(src._probe_headers(self.data)['etag'],'"NEW"')

if __name__=='__main__':unittest.main()
