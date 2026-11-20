
import json
import unittest
from io import BytesIO
from unittest.mock import patch

import update_checker


class VersionComparisonTests(unittest.TestCase):
    def test_newer_version_is_detected(self):
        self.assertTrue(
            update_checker.is_newer_version("1.1.0", "1.0.0")
        )

    def test_same_version_is_not_newer(self):
        self.assertFalse(
            update_checker.is_newer_version("1.0.0", "1.0.0")
        )

    def test_older_version_is_not_newer(self):
        self.assertFalse(
            update_checker.is_newer_version("0.9.0", "1.0.0")
        )

    def test_v_prefix_is_supported(self):
        self.assertTrue(
            update_checker.is_newer_version("v1.2.0", "1.1.0")
        )


class ReleaseAssetTests(unittest.TestCase):
    release_page = f"{update_checker.RELEASES_PAGE}/tag/v1.2.0"
    download_prefix = f"{update_checker.RELEASES_PAGE}/download/v1.2.0"
    versioned = f"{download_prefix}/PDF-Merger-Compressor-1.2.0.exe"
    unversioned = f"{download_prefix}/PDF-Merger-Compressor.exe"

    def check_release(self, assets):
        payload = {"tag_name": "v1.2.0", "html_url": self.release_page, "assets": assets}
        with patch.object(update_checker, "urlopen", return_value=BytesIO(json.dumps(payload).encode())):
            return update_checker.check_for_update("1.1.0")

    def test_versioned_executable_takes_priority(self):
        result = self.check_release([
            {"state": "uploaded", "browser_download_url": self.unversioned},
            {"state": "uploaded", "browser_download_url": self.versioned},
        ])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["download_url"], self.versioned)

    def test_unversioned_executable_is_supported(self):
        result = self.check_release([{"state": "uploaded", "browser_download_url": self.unversioned}])
        self.assertEqual(result["download_url"], self.unversioned)

    def test_release_page_remains_available_without_a_matching_executable(self):
        result = self.check_release([
            {"state": "starter", "browser_download_url": self.versioned},
            {"state": "uploaded", "browser_download_url": self.versioned.replace("github.com", "example.org")},
            {"state": "uploaded", "browser_download_url": self.versioned.replace("/v1.2.0/", "/v1.1.0/")},
            {"state": "uploaded", "browser_download_url": f"{self.download_prefix}/source.zip"},
        ])
        self.assertTrue(result["update_available"])
        self.assertIsNone(result["download_url"])
        self.assertEqual(result["release_url"], self.release_page)


if __name__ == "__main__":
    unittest.main()
