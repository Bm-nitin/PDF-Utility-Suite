
import unittest
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


if __name__ == "__main__":
    unittest.main()
