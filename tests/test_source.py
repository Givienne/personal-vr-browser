import unittest
import vrsrc_source as source

class AccessGateTests(unittest.TestCase):
    def test_no_public_mirror_urls(self):
        self.assertFalse(hasattr(source, "remote_url"))
        self.assertFalse(hasattr(source, "list_remote_files"))

if __name__ == "__main__": unittest.main()
