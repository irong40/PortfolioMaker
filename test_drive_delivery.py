"""Tests for drive_delivery — delivery file collection policy + auth check."""

import pytest
from unittest.mock import MagicMock, patch

from google.auth.exceptions import RefreshError

import drive_delivery
from drive_delivery import (
    DriveUnavailableError, collect_delivery_files, is_authenticated,
)


class TestIsAuthenticated:
    """is_authenticated must only return False when re-auth is truly needed;
    transient refresh failures raise DriveUnavailableError instead."""

    def _creds(self, mocker, refresh_side_effect=None):
        creds = MagicMock()
        creds.valid = False
        creds.expired = True
        creds.refresh_token = "rt"
        if refresh_side_effect is not None:
            creds.refresh.side_effect = refresh_side_effect
        mocker.patch.object(drive_delivery.Credentials,
                            "from_authorized_user_file", return_value=creds)
        mocker.patch.object(drive_delivery, "TOKEN_PATH",
                            MagicMock(exists=MagicMock(return_value=True)))
        mocker.patch.object(drive_delivery, "_save_token")
        return creds

    def test_no_token_file_needs_reauth(self, mocker):
        mocker.patch.object(drive_delivery, "TOKEN_PATH",
                            MagicMock(exists=MagicMock(return_value=False)))
        assert is_authenticated() is False

    def test_refresh_success(self, mocker):
        self._creds(mocker)
        assert is_authenticated() is True

    def test_revoked_grant_needs_reauth(self, mocker):
        self._creds(mocker, RefreshError(
            "invalid_grant: Token has been expired or revoked."))
        assert is_authenticated() is False

    def test_transient_failure_raises_not_false(self, mocker):
        self._creds(mocker, ConnectionError("connection reset"))
        with pytest.raises(DriveUnavailableError):
            is_authenticated(retry_delay=0)

    def test_transient_failure_retries_once_then_succeeds(self, mocker):
        creds = self._creds(mocker,
                            [ConnectionError("connection reset"), None])
        assert is_authenticated(retry_delay=0) is True
        assert creds.refresh.call_count == 2


class TestCollectDeliveryFiles:
    def test_skips_underscore_folders(self, tmp_path):
        (tmp_path / "report.pdf").write_text("x")
        (tmp_path / "gis").mkdir()
        (tmp_path / "gis" / "mission.kml").write_text("x")
        (tmp_path / "_gis").mkdir()
        (tmp_path / "_gis" / "photo_points.csv").write_text("x")
        (tmp_path / "_report_thumbs").mkdir()
        (tmp_path / "_report_thumbs" / "thumb.jpg").write_text("x")
        files = {f.relative_to(tmp_path).as_posix()
                 for f in collect_delivery_files(tmp_path)}
        assert files == {"report.pdf", "gis/mission.kml"}

    def test_skips_nested_underscore_folders(self, tmp_path):
        (tmp_path / "panoramas" / "_work").mkdir(parents=True)
        (tmp_path / "panoramas" / "_work" / "tmp.jpg").write_text("x")
        (tmp_path / "panoramas" / "pano.jpg").write_text("x")
        files = {f.relative_to(tmp_path).as_posix()
                 for f in collect_delivery_files(tmp_path)}
        assert files == {"panoramas/pano.jpg"}

    def test_empty_dir(self, tmp_path):
        assert collect_delivery_files(tmp_path) == []
