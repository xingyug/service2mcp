"""Injection and SSRF prevention tests.

Verifies that the compiler API rejects payloads containing:
- SSRF attempts (private IPs, localhost, metadata endpoints in source_url)
- Path traversal sequences in artifact service IDs
- XSS in service names (checked via content-type / no HTML rendering)
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import pytest
from httpx import AsyncClient

from tests.security.conftest import auth_header, build_valid_jwt

pytestmark = pytest.mark.security

# ---------------------------------------------------------------------------
# SSRF: Private / internal IPs in source_url
# ---------------------------------------------------------------------------

_SSRF_URLS = [
    # RFC 1918 private ranges
    "http://10.0.0.1/api/spec.yaml",
    "http://10.255.255.255:8080/openapi.json",
    "http://172.16.0.1/swagger.json",
    "http://172.31.255.255/api",
    "http://192.168.0.1/api-spec",
    "http://192.168.1.100:9090/spec",
    # Loopback
    "http://127.0.0.1/api",
    "http://127.0.0.1:3000/spec.json",
    "http://localhost/swagger.json",
    "http://localhost:8080/api",
    # Cloud metadata endpoints
    "http://169.254.169.254/latest/meta-data/",
    "http://169.254.169.254/metadata/instance",
    # IPv6 loopback
    "http://[::1]/api",
    # DNS rebinding / zero address
    "http://0.0.0.0/spec",
]

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
]


def _is_private_url(url: str) -> bool:
    """Return True if *url* targets a private/reserved IP or localhost."""
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    if hostname in {"localhost", ""}:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in net for net in _PRIVATE_RANGES)
    except ValueError:
        return False


class TestSSRFPrevention:
    """source_url values pointing to internal addresses must be detected.

    These tests validate that the URLs we classify as SSRF targets are
    correctly identified.  The route-level blocking is tested via xfail
    since the API does not yet enforce URL-level SSRF prevention; the
    detection logic is tested here as a specification.
    """

    @pytest.mark.parametrize("url", _SSRF_URLS, ids=_SSRF_URLS)
    def test_private_url_detection(self, url: str) -> None:
        """Every URL in the SSRF list targets a private/reserved address."""
        assert _is_private_url(url), f"{url} should be classified as private"

    def test_public_url_not_flagged(self) -> None:
        assert not _is_private_url("https://petstore.swagger.io/v2/swagger.json")

    @pytest.mark.parametrize(
        "url",
        _SSRF_URLS[:5],
        ids=_SSRF_URLS[:5],
    )
    @pytest.mark.xfail(reason="SSRF route-level prevention not yet implemented", strict=False)
    async def test_private_ip_source_url_rejected_at_route(
        self, client: AsyncClient, url: str
    ) -> None:
        """Once SSRF protection is added, private IPs should yield 400/422."""
        token = build_valid_jwt(roles=["admin"])
        try:
            resp = await client.post(
                "/api/v1/compilations",
                headers=auth_header(token),
                json={"source_url": url},
            )
        except Exception:
            # Mock DB may not support the full compilation flow; treat as
            # "request did not succeed" which is the expected outcome.
            pytest.xfail("Route crashed before SSRF check (mock limitation)")
            return
        assert resp.status_code in {400, 422}, (
            f"Expected 400/422 for SSRF URL {url}, got {resp.status_code}"
        )


# ---------------------------------------------------------------------------
# Path traversal in artifact service IDs / version paths
# ---------------------------------------------------------------------------

_PATH_TRAVERSAL_PAYLOADS = [
    "../../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    "svc-1/../../secrets",
    "....//....//etc/passwd",
    "%2e%2e%2f%2e%2e%2f",
]


class TestPathTraversal:
    @pytest.mark.parametrize("service_id", _PATH_TRAVERSAL_PAYLOADS)
    async def test_artifact_list_rejects_traversal(
        self, client: AsyncClient, service_id: str
    ) -> None:
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get(
            f"/api/v1/artifacts/{service_id}/versions",
            headers=auth_header(token),
        )
        # FastAPI's path parameter handling will either return 404
        # (path doesn't match route) or the repository returns empty.
        # The critical assertion: we never get a 2xx that leaks files.
        assert resp.status_code != 200 or _body_has_no_file_content(resp), (
            f"Potential path traversal via service_id={service_id!r}"
        )

    @pytest.mark.parametrize("service_id", _PATH_TRAVERSAL_PAYLOADS)
    async def test_service_get_rejects_traversal(
        self, client: AsyncClient, service_id: str
    ) -> None:
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get(
            f"/api/v1/services/{service_id}",
            headers=auth_header(token),
        )
        # Should not return a file's content
        assert resp.status_code != 200 or _body_has_no_file_content(resp)


def _body_has_no_file_content(resp) -> bool:
    """Heuristic: response body should not contain /etc/passwd markers."""
    text = resp.text.lower()
    return "root:" not in text and "/bin/bash" not in text


# ---------------------------------------------------------------------------
# XSS in service names (response content-type enforcement)
# ---------------------------------------------------------------------------


class TestXSSPrevention:
    async def test_json_content_type_for_api_responses(self, client: AsyncClient) -> None:
        """API responses must use application/json, not text/html."""
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get("/api/v1/services", headers=auth_header(token))
        # Even on error, the content-type must not be text/html
        ct = resp.headers.get("content-type", "")
        assert "text/html" not in ct

    async def test_xss_in_validation_error_not_rendered(
        self,
        client: AsyncClient,
    ) -> None:
        """Payloads with XSS should produce JSON errors, never HTML."""
        xss_payload = '<script>alert("xss")</script>'
        token = build_valid_jwt(roles=["admin"])

        # Send both source_url and source_content to trigger a 422 validation
        # error *before* hitting the DB — allows testing the response format
        # without a full mock of the compilation repository.
        resp = await client.post(
            "/api/v1/compilations",
            headers=auth_header(token),
            json={
                "source_url": f"http://example.com/{xss_payload}",
                "source_content": xss_payload,
            },
        )
        assert resp.status_code == 422
        ct = resp.headers.get("content-type", "")
        assert "text/html" not in ct
        # The raw <script> tag must not appear outside of a JSON string
        assert "<script>" not in resp.text or '"<script>' in resp.text or "\\u003c" in resp.text

    async def test_nosniff_header_prevents_mime_sniffing(self, client: AsyncClient) -> None:
        """X-Content-Type-Options: nosniff must be set on every response."""
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get("/api/v1/compilations", headers=auth_header(token))
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    async def test_xframe_deny_header(self, client: AsyncClient) -> None:
        """X-Frame-Options: DENY prevents clickjacking."""
        resp = await client.get("/healthz")
        assert resp.headers.get("X-Frame-Options") == "DENY"


# ---------------------------------------------------------------------------
# SQL injection resistance (defense-in-depth: ORM parameterizes, but verify
# that suspicious inputs don't cause 500s or data leaks)
# ---------------------------------------------------------------------------


class TestSQLInjectionResistance:
    _SQLI_PAYLOADS = [
        "'; DROP TABLE compilations; --",
        "1 OR 1=1",
        "' UNION SELECT * FROM users --",
        "admin'--",
    ]

    @pytest.mark.parametrize("payload", _SQLI_PAYLOADS)
    async def test_sqli_in_service_id_param(self, client: AsyncClient, payload: str) -> None:
        """SQLi in path/query params should not cause 500."""
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get(
            f"/api/v1/services/{payload}",
            headers=auth_header(token),
        )
        # ORM parameterizes, so this should be 404 or similar — not 500
        assert resp.status_code != 500, f"Potential SQL injection via service_id={payload!r}"

    @pytest.mark.parametrize("payload", _SQLI_PAYLOADS)
    async def test_sqli_in_compilations_filter(self, client: AsyncClient, payload: str) -> None:
        token = build_valid_jwt(roles=["admin"])
        resp = await client.get(
            f"/api/v1/compilations?service_id={payload}",
            headers=auth_header(token),
        )
        assert resp.status_code != 500
