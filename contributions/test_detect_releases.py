import unittest
from detect_releases import candidates_for, version_code

class TestDetector(unittest.TestCase):
    def setUp(self):
        self.catalog = [{"id":"org.example.one","packageName":"org.example.one","version":"1014"},
                        {"id":"org.example.two","packageName":"org.example.two","version":"5"}]

    def test_newer_and_missing(self):
        manifest = [{"package":"org.example.one","versionCode":1015},
                    {"package":"org.example.two","versionCode":5},
                    {"package":"org.example.three","versionCode":1}]
        result = candidates_for(self.catalog, manifest)
        self.assertEqual([(x['package'], x['installed'], x['catalog']) for x in result],
                         [('org.example.one',1015,1014),('org.example.three',1,None)])

    def test_older_is_not_newer(self):
        self.assertEqual(candidates_for(self.catalog,[{"package":"org.example.one","versionCode":999}]),[])

    def test_duplicate_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate manifest"):
            candidates_for(self.catalog,[{"package":"org.example.one","versionCode":1015}]*2)

    def test_version_validation(self):
        for value in ('1.2', 'abc', -1, True, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                version_code(value)

if __name__ == '__main__':
    unittest.main()
