import unittest
from version_audit import find_updates, installed_versions


class VersionAuditTests(unittest.TestCase):
    def test_versions_from_adb_output(self):
        text = "package:org.example.one versionCode:10\npackage:org.example.two versionCode:4\nnot a package\n"
        self.assertEqual(installed_versions(text), {"org.example.one": 10, "org.example.two": 4})

    def test_newer_and_missing_only(self):
        catalog = [
            {"id": "org.example.one", "version": "9"},
            {"id": "org.example.two", "version": "5"},
        ]
        found = find_updates(catalog, {"org.example.one": 10, "org.example.two": 4, "org.example.three": 1})
        self.assertEqual([x["package"] for x in found], ["org.example.one", "org.example.three"])


if __name__ == "__main__":
    unittest.main()
