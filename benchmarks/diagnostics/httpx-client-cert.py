"""Diagnostic test template; no network or real credentials are required."""

import httpx
import pytest


@pytest.mark.filterwarnings("ignore:.*cert=.*:DeprecationWarning")
def test_disabled_server_verification_does_not_ignore_client_certificate(tmp_path):
    missing_certificate = str(tmp_path / "missing-client-cert.pem")
    with pytest.raises(FileNotFoundError):
        httpx.create_ssl_context(verify=False, cert=missing_certificate)
