import io
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vrsrc_runtime as rk

NOW = datetime(2026, 11, 25, 18, 0, tzinfo=timezone.utc)

def response(*, key='SECRET-TEMP-1', refresh=180, expires=3600, base='https://go.srcdl1.xyz'):
    return {'apiKey':key, 'baseUri':base,
            'refreshAfter':(NOW+timedelta(seconds=refresh)).isoformat(),
            'expiresAt':(NOW+timedelta(seconds=expires)).isoformat()}

class FakeResponse(io.BytesIO):
    def __init__(self, obj, url=rk.KEY_ENDPOINT, status=200):
        super().__init__(json.dumps(obj).encode()); self.status=status; self._url=url
    def geturl(self):return self._url

class Client:
    def __init__(self, replies):self.replies=list(replies);self.calls=[]
    def open(self, request, timeout=0):
        self.calls.append((request,timeout))
        reply=self.replies.pop(0)
        if isinstance(reply,Exception):raise reply
        return FakeResponse(reply)

class RuntimeKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'install-id'
        self.now=NOW

    def client(self,reply):
        return rk.RuntimeKeys(client_id='public-ID',build_secret='private-build-secret',
               install_file=self.path,app_version='1.2.3',opener=reply,clock=lambda:self.now)

    def test_key_request_and_cache(self):
        opener=Client([response()]); client=self.client(opener)
        self.assertEqual(client.get(),'SECRET-TEMP-1')
        self.assertEqual(client.get(),'SECRET-TEMP-1')
        self.assertEqual(len(opener.calls),1)
        req=opener.calls[0][0]
        self.assertEqual(req.full_url,rk.KEY_ENDPOINT)
        self.assertEqual(json.loads(req.data)['build_secret'],'private-build-secret')
        self.assertEqual(json.loads(req.data)['app_version'],'1.2.3')
        value=self.path.read_text().strip()
        self.assertGreaterEqual(len(value),8)
        self.assertEqual(rk.load_install_id(self.path),value)
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)

    def test_refresh_and_expiration(self):
        opener=Client([response(),response(key='SECRET-TEMP-2',refresh=400)])
        client=self.client(opener);self.assertEqual(client.get(),'SECRET-TEMP-1')
        self.now=NOW+timedelta(seconds=220)
        self.assertEqual(client.get(),'SECRET-TEMP-2')
        self.assertEqual(len(opener.calls),2)

    def test_no_redirects(self):
        response_obj=rk.NoRedirects().redirect_request(None,None,302,'Redirect',{},'https://evil.example')
        self.assertIsNone(response_obj)

    def test_reject_unapproved_mirror_origin(self):
        with self.assertRaisesRegex(rk.AuthenticationUnavailable,'unexpected mirror origin'):
            self.client(Client([response(base='https://evil.example')])).get()

    def test_403_invalid_suspends(self):
        bad=HTTPError(rk.KEY_ENDPOINT,403,'Forbidden',{},io.BytesIO(b'{"reason":"suspended"}'))
        opener=Client([bad]); client=self.client(opener)
        with self.assertRaisesRegex(rk.AuthenticationUnavailable,'rejected'):
            client.get()
        with self.assertRaisesRegex(rk.AuthenticationUnavailable,'suspended'):
            client.get()
        self.assertEqual(len(opener.calls),1)

    def test_503_key_pending_backoff(self):
        err=HTTPError(rk.KEY_ENDPOINT,503,'Unavailable',{},io.BytesIO(b'{"reason":"key_pending"}'))
        opener=Client([err]);client=self.client(opener)
        with self.assertRaisesRegex(rk.AuthenticationUnavailable,'pending'):
            client.get()
        with self.assertRaisesRegex(rk.AuthenticationUnavailable,'cooling'):
            client.get()
        self.assertEqual(len(opener.calls),1)

    def test_bad_expires_at_rejected(self):
        obj=response();obj['expiresAt']='2020-01-01T00:00:00.000Z'
        with self.assertRaisesRegex(rk.AuthenticationUnavailable,'lifetimes'):
            self.client(Client([obj])).get()

    def test_missing_credentials_rejected(self):
        c=rk.RuntimeKeys(client_id='',build_secret='',install_file=self.path)
        self.assertFalse(c.ready())
        with self.assertRaises(rk.AuthenticationUnavailable):c.get()

if __name__=='__main__': unittest.main()
