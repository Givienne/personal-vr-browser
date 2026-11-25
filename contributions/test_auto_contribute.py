import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from auto_contribute import catalog_codes, discover, scan_once
from provider_upload import provider_destination, upload_once

class AutoContributionTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.catalog=self.root/'catalog.json'
        self.catalog.write_text(json.dumps([{'packageName':'org.example.app','version':'7'}]))
        self.incoming=self.root/'incoming';self.incoming.mkdir(mode=0o700)
        (self.incoming/'hello.apk').write_bytes(b'test')
        self.state=self.root/'sent.json'

    def discover(self,*args):
        return discover(*args, inspect=lambda _:('org.example.app',8))

    def test_detects_new_apk_without_manual_manifest(self):
        found=self.discover(self.catalog,self.incoming)
        self.assertEqual(len(found),1)
        self.assertEqual((found[0]['package'],found[0]['versionCode']),('org.example.app',8))
        self.assertEqual(found[0]['catalogVersionCode'],7)

    def test_older_version_ignored(self):
        result=discover(self.catalog,self.incoming,inspect=lambda _:('org.example.app',7))
        self.assertEqual(result,[])

    def test_unknown_app_detected(self):
        result=discover(self.catalog,self.incoming,inspect=lambda _:('org.another.app',2))
        self.assertEqual(result[0]['catalogVersionCode'],None)

    def test_dry_run_no_upload(self):
        uploader=Mock()
        found=scan_once(self.catalog,self.incoming,self.state,discoverer=self.discover,uploader=uploader)
        self.assertEqual(len(found),1)
        uploader.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_upload_disabled_by_default(self):
        with self.assertRaises(PermissionError):
            scan_once(self.catalog,self.incoming,self.state,send=True,discoverer=self.discover)

    def test_authorized_upload_deduplicates(self):
        sender=Mock(return_value='provider:org.example.app/8/hello.apk')
        args=dict(catalog=self.catalog,incoming=self.incoming,state_file=self.state,
                  send=True,upload_config=self.root/'upload.config',layout_file=self.root/'layout.json',
                  environ={'VRSRC_ACCESS_APPROVED':'1','VRSRC_UPLOAD_AUTHORIZED':'1'},
                  discoverer=self.discover,uploader=sender)
        self.assertEqual(len(scan_once(**args)),1)
        self.assertEqual(len(scan_once(**args)),0)
        sender.assert_called_once()
        self.assertEqual(len(json.loads(self.state.read_text())['sent']),1)
        self.assertEqual(self.state.stat().st_mode&0o777,0o600)

    def test_symlink_skipped(self):
        (self.incoming/'linked.apk').symlink_to(self.incoming/'hello.apk')
        self.assertEqual(len(self.discover(self.catalog,self.incoming)),1)

    def test_reject_world_writable_directory(self):
        self.incoming.chmod(0o777)
        with self.assertRaisesRegex(ValueError,'writable'):
            self.discover(self.catalog,self.incoming)

class ProviderConfigTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.config=self.root/'upload.config'
        self.config.write_text('[vrsrc_received]\ntype = sftp\nhost = example.invalid\n')
        self.config.chmod(0o600)
        self.layout=self.root/'layout.json'
        self.layout.write_text(json.dumps({'remote':'vrsrc_received','relativePathTemplate':'incoming/{package}/{versionCode}/{filename}'}))
        self.layout.chmod(0o600)

    def test_provider_upload_config_only(self):
        cfg,destination=provider_destination(self.config,self.layout,package='org.example.app',versionCode=8,filename='hello.apk')
        self.assertEqual(destination,'vrsrc_received:incoming/org.example.app/8/hello.apk')
        self.assertEqual(cfg,self.config)

    def test_private_config_enforced(self):
        self.config.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'owner-only'):
            provider_destination(self.config,self.layout,package='org.example.app',versionCode=8,filename='hello.apk')

    def test_reject_relative_traversal(self):
        self.layout.write_text(json.dumps({'remote':'vrsrc_received','relativePathTemplate':'../{package}/{versionCode}/{filename}'}))
        with self.assertRaisesRegex(ValueError,'Unsafe provider'):
            provider_destination(self.config,self.layout,package='org.example.app',versionCode=8,filename='hello.apk')

    def test_reject_arbitrary_destination(self):
        self.layout.write_text(json.dumps({'remote':'elsewhere','relativePathTemplate':'{package}/{versionCode}/{filename}'}))
        with self.assertRaisesRegex(ValueError,'missing'):
            provider_destination(self.config,self.layout,package='org.example.app',versionCode=8,filename='hello.apk')

    def test_mocked_provider_transfer_uses_only_configured_remote(self):
        from unittest.mock import patch
        from contribution_queue import sha256
        from zipfile import ZipFile
        file=self.root/'hello.apk'
        with ZipFile(file,'w') as archive:
            archive.writestr('AndroidManifest.xml',b'dummy binary manifest')
            archive.writestr('classes.dex',b'mock dex')
        item={'package':'org.example.app','versionCode':8,'file':str(file),'sha256':sha256(file)}
        runner=Mock(return_value=Mock(returncode=0))
        with patch('apk_validation.require_apk_identity') as inspect:
            destination=upload_once(item=item,upload_config=self.config,
                                    layout_file=self.layout,
                                    environ={'VRSRC_ACCESS_APPROVED':'1','VRSRC_UPLOAD_AUTHORIZED':'1'},
                                    runner=runner,rclone='/usr/bin/rclone')
        self.assertEqual(destination,'vrsrc_received:incoming/org.example.app/8/hello.apk')
        command=runner.call_args.args[0]
        self.assertIn('--immutable',command)
        self.assertNotIn('X-API-Key',repr(command))
        self.assertEqual(command[5],destination)
        inspect.assert_called_once()

    def test_upload_command_only_when_approved(self):
        file=self.root/'hello.apk';file.write_bytes(b'fake')
        item={'package':'org.example.app','versionCode':8,'file':str(file),'sha256':'0'*64}
        with self.assertRaises(PermissionError):
            upload_once(item=item,upload_config=self.config,layout_file=self.layout,environ={})

if __name__=='__main__':unittest.main()
