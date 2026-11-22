
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
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
    setup = f"{download_prefix}/PDF-Utility-Suite-Setup-1.2.0.exe"

    def check_release(self, assets, *, installed=False):
        payload = {"tag_name": "v1.2.0", "html_url": self.release_page, "assets": assets}
        with (
            patch.object(update_checker, "urlopen", return_value=BytesIO(json.dumps(payload).encode())),
            patch.object(update_checker, "_is_windows_install", return_value=installed),
        ):
            return update_checker.check_for_update("1.1.0")

    def test_versioned_executable_takes_priority(self):
        result = self.check_release([
            {"state": "uploaded", "browser_download_url": self.unversioned},
            {"state": "uploaded", "browser_download_url": self.setup},
            {"state": "uploaded", "browser_download_url": self.versioned},
        ])
        self.assertTrue(result["update_available"])
        self.assertEqual(result["download_url"], self.versioned)
        self.assertFalse(result["download_is_setup"])

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

    def test_installed_app_gets_setup_even_when_portable_is_available(self):
        result = self.check_release([
            {"state": "uploaded", "browser_download_url": self.versioned},
            {"state": "uploaded", "browser_download_url": self.setup},
        ], installed=True)
        self.assertEqual(result["download_url"], self.setup)
        self.assertTrue(result["download_is_setup"])

    def test_installed_app_falls_back_to_release_page_without_a_valid_setup(self):
        result = self.check_release([
            {"state": "uploaded", "browser_download_url": self.versioned},
            {"state": "starter", "browser_download_url": self.setup},
            {"state": "uploaded", "browser_download_url": self.setup.replace("github.com", "example.org")},
        ], installed=True)
        self.assertIsNone(result["download_url"])
        self.assertFalse(result["download_is_setup"])
        self.assertEqual(result["release_url"], self.release_page)

    def test_portable_app_falls_back_to_release_page_for_setup_only_release(self):
        result = self.check_release([{"state": "uploaded", "browser_download_url": self.setup}])
        self.assertIsNone(result["download_url"])
        self.assertEqual(result["release_url"], self.release_page)


@unittest.skipUnless(sys.platform == "win32", "The install record is specific to Windows.")
class WindowsInstallTests(unittest.TestCase):
    def test_only_the_exe_in_the_registered_folder_counts_as_installed(self):
        import winreg

        install_dir = Path(r"C:\Custom PDF Tools")
        for directory in (install_dir, Path(r"C:\Portable PDF Tools")):
            executable = directory / "PDF-Merger-Compressor.exe"
            with (
                self.subTest(executable=executable),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
                patch.object(winreg, "OpenKey") as open_key,
                patch.object(winreg, "QueryValueEx", return_value=(str(install_dir), winreg.REG_SZ)),
            ):
                self.assertEqual(update_checker._is_windows_install(), directory == install_dir)
                open_key.assert_called_once_with(
                    winreg.HKEY_LOCAL_MACHINE, update_checker._UNINSTALL_KEY, 0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                )

    def test_frozen_app_without_install_record_is_portable(self):
        import winreg

        with (
            patch.object(sys, "frozen", True, create=True),
            patch.object(winreg, "OpenKey", side_effect=FileNotFoundError),
        ):
            self.assertFalse(update_checker._is_windows_install())


if __name__ == "__main__":
    unittest.main()
