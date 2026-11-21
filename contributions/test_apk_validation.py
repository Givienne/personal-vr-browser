import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import zipfile
from apk_validation import require_apk_container, require_apk_identity

class TestApkValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / 'test.apk'

    def make_apk(self, extra='classes.dex'):
        with zipfile.ZipFile(self.file, 'w') as z:
            z.writestr('AndroidManifest.xml', b'\x03\x00fixture')
            z.writestr(extra, b'content')

    def test_plaintext_renamed_apk_rejected(self):
        self.file.write_text('this is not an APK')
        with self.assertRaisesRegex(ValueError, 'Invalid APK'):
            require_apk_container(self.file)

    def test_missing_manifest_rejected(self):
        with zipfile.ZipFile(self.file, 'w') as z:
            z.writestr('classes.dex', b'123')
        with self.assertRaisesRegex(ValueError, 'AndroidManifest'):
            require_apk_container(self.file)

    def test_unsafe_path_rejected(self):
        self.make_apk('../surprise.txt')
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            require_apk_container(self.file)

    def test_structural_check_valid_fixture(self):
        self.make_apk()
        require_apk_container(self.file)

    def test_aapt2_metadata_match(self):
        self.make_apk()
        run = Mock(return_value=Mock(returncode=0, stdout="package: name='org.example.app' versionCode='42' versionName='1'\n"))
        require_apk_identity(self.file, 'org.example.app', 42, aapt2='/test/aapt2', runner=run)
        run.assert_called_once()

    def test_aapt2_wrong_package_rejected(self):
        self.make_apk()
        run = Mock(return_value=Mock(returncode=0, stdout="package: name='org.other.app' versionCode='42' versionName='1'\n"))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            require_apk_identity(self.file, 'org.example.app', 42, aapt2='/test/aapt2', runner=run)

    def test_aapt2_wrong_version_rejected(self):
        self.make_apk()
        run = Mock(return_value=Mock(returncode=0, stdout="package: name='org.example.app' versionCode='41' versionName='1'\n"))
        with self.assertRaisesRegex(ValueError, 'does not match'):
            require_apk_identity(self.file, 'org.example.app', 42, aapt2='/test/aapt2', runner=run)

    def test_requires_aapt2(self):
        self.make_apk()
        from unittest.mock import patch
        with patch('apk_validation.shutil.which', return_value=None), self.assertRaisesRegex(ValueError, 'aapt2 is required'):
            require_apk_identity(self.file, 'org.example.app', 42)

if __name__ == '__main__':
    unittest.main()
