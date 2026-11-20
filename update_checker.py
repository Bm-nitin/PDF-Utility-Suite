
"""Check GitHub Releases for newer application versions.

This module only retrieves release metadata. It never downloads or
executes application binaries.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from config import APP_VERSION

GITHUB_REPOSITORY = "Bm-nitin/PDF-Utility-Suite"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
RELEASES_PAGE = f"https://github.com/{GITHUB_REPOSITORY}/releases"
TIMEOUT_SECONDS = 6


class UpdateCheckError(Exception):
    """Raised when the update check cannot be completed."""


def _version_tuple(version: str) -> tuple[int, ...]:
    """Convert a numeric version such as v1.2.3 to comparable integers."""
    value = version.strip()
    if value[:1].lower() == "v":
        value = value[1:]

    # Ignore prerelease/build suffixes for this basic numeric comparison.
    value = re.split(r"[-+]", value, maxsplit=1)[0]
    parts = value.split(".")

    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid version: {version!r}")

    return tuple(int(part) for part in parts)


def is_newer_version(version: str, current_version: str = APP_VERSION) -> bool:
    """Return True if version is numerically newer than current_version."""
    candidate = _version_tuple(version)
    current = _version_tuple(current_version)
    width = max(len(candidate), len(current))
    return candidate + (0,) * (width - len(candidate)) > (
        current + (0,) * (width - len(current))
    )


def _rate_limit_message(headers) -> str:
    retry_after = headers.get("Retry-After")
    if retry_after:
        return f"GitHub rate limit reached. Retry after {retry_after} seconds."

    reset = headers.get("X-RateLimit-Reset")
    if reset and reset.isdigit():
        reset_time = datetime.fromtimestamp(
            int(reset), tz=timezone.utc
        ).astimezone()
        return f"GitHub rate limit reached. Try again after {reset_time:%H:%M:%S %Z}."

    return "GitHub rate limit reached. Please try again later."


def _windows_download_url(assets: object, tag: str) -> str | None:
    if not isinstance(assets, list):
        return None

    uploaded_urls = {
        asset["browser_download_url"] for asset in assets
        if isinstance(asset, dict) and asset.get("state") == "uploaded"
        and isinstance(asset.get("browser_download_url"), str)
    }
    prefix = f"{RELEASES_PAGE}/download/{quote(tag, safe='')}"
    versioned = f"{prefix}/PDF-Merger-Compressor-{quote(tag.lstrip('vV'), safe='')}.exe"
    unversioned = f"{prefix}/PDF-Merger-Compressor.exe"
    if versioned in uploaded_urls:
        return versioned
    if unversioned in uploaded_urls:
        return unversioned
    return None


def check_for_update(current_version: str = APP_VERSION) -> dict:
    """Check the latest published release and return update information.

    Returns update_available, version, release_url, notes, and the optional
    Windows download_url. Raises UpdateCheckError for network/API/response failures.
    """
    request = Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "PDF-Merger-Compressor-Update-Checker",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (403, 429):
            remaining = exc.headers.get("X-RateLimit-Remaining")
            if exc.code == 429 or remaining == "0":
                raise UpdateCheckError(
                    _rate_limit_message(exc.headers)
                ) from exc
            raise UpdateCheckError(
                "GitHub denied the release request (HTTP 403). "
                "Check repository access and try again later."
            ) from exc

        if exc.code == 404:
            raise UpdateCheckError(
                "No published GitHub release was found for this repository."
            ) from exc

        raise UpdateCheckError(
            f"GitHub returned HTTP {exc.code} while checking for updates."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UpdateCheckError(
            "Could not reach GitHub. Check your internet connection and try again."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateCheckError(
            "GitHub returned an unreadable release response."
        ) from exc

    if not isinstance(payload, dict):
        raise UpdateCheckError("GitHub returned an invalid release response.")

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        raise UpdateCheckError("The latest release has no valid version tag.")

    try:
        newer = is_newer_version(tag, current_version)
    except ValueError as exc:
        raise UpdateCheckError(
            f"GitHub release has an unsupported version tag: {tag!r}."
        ) from exc

    release_url = payload.get("html_url", "")
    trusted_prefix = f"https://github.com/{GITHUB_REPOSITORY}/releases/"
    if not isinstance(release_url, str) or not release_url.startswith(trusted_prefix):
        release_url = f"{RELEASES_PAGE}/latest"

    notes = payload.get("body", "")
    if not isinstance(notes, str):
        notes = ""

    return {
        "update_available": newer,
        "version": tag.lstrip("vV"),
        "release_url": release_url,
        "download_url": _windows_download_url(payload.get("assets"), tag) if newer else None,
        "notes": notes,
    }
