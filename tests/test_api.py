"""Mocked HTTP boundary, exact endpoint contracts, privacy, and redirects."""

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from yarl import URL

from custom_components.healthlog.api import (
    API,
    APIError,
    AuthError,
    PermissionError,
    normalize_url,
    retry_seconds,
    validate_token,
)
from custom_components.healthlog.models import METRICS
from custom_components.healthlog.transport import BATCH_TYPES, Transport, durable_identity

from .conftest import TOKEN, measurement


def http_session(status=200, body=None, headers=None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.json.return_value = body or {"data": {}, "error": None}
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    context.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = context
    return session, response


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://@example.com",
        "https://example.com/?token=x",
        "https://example.com/#x",
        "https://example.com:invalid",
        "https://example.com:99999",
        "https://example.com:0",
        "https://example.com:",
        "https://example.com?",
        "https://example.com#",
        "https://example.com/\n",
        "https://example.com/%0d%0aevil",
        "https://example.com/%zz",
        "https://example.com/%5cevil",
        "https://example..com",
        "https://-example.com",
        "https://example.com-",
        "https://999.999.999.999",
        "https://[example.com]",
        "https://::1",
        "https:/example.com",
        "https:example.com",
        "mailto:user@example.com",
        "ftp://example.com",
        "ws://example.com",
        "//example.com",
        "https://exam\nple.com",
        "https://example.com\\evil",
        "",
        "file:///x",
    ],
)
def test_bad_urls(url):
    with pytest.raises(ValueError):
        normalize_url(url)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("healthlog.local", "https://healthlog.local"),
        ("healthlog.local:8443/healthlog/", "https://healthlog.local:8443/healthlog"),
        ("healthlog:3000/base", "https://healthlog:3000/base"),
        ("192.168.1.20:8443/base", "https://192.168.1.20:8443/base"),
        ("[2001:db8::1]:8443/base", "https://[2001:db8::1]:8443/base"),
        ("HTTPS://EXAMPLE.COM:443/base/", "https://example.com/base"),
        (" https://healthlog.local/base/ ", "https://healthlog.local/base"),
        ("https://example.com/a%2Fb/", "https://example.com/a%2Fb"),
        ("https://bücher.example", "https://xn--bcher-kva.example"),
    ],
)
def test_url_normalization(value, expected):
    assert normalize_url(value) == expected
    assert normalize_url(expected) == expected


def test_http_requires_explicit_boolean_opt_in():
    url = "http://healthlog.local:3000/healthlog/"
    for opt_in in (False, None, "true", "false", 1):
        with pytest.raises(ValueError):
            normalize_url(url, allow_insecure_http=opt_in)
    assert normalize_url(url, allow_insecure_http=True) == "http://healthlog.local:3000/healthlog"
    assert (
        normalize_url("healthlog.local:3000", allow_insecure_http=True)
        == "https://healthlog.local:3000"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///x",
        "http://user:pass@example.com",
        "http://example.com:invalid",
        "http://example.com/?token=x",
    ],
)
def test_http_opt_in_does_not_relax_other_url_validation(url):
    with pytest.raises(ValueError):
        normalize_url(url, allow_insecure_http=True)


def test_runtime_http_policy():
    session, _ = http_session()
    with pytest.raises(ValueError):
        API(session, "http://healthlog.local", TOKEN)
    assert (
        API(session, "http://healthlog.local:3000/base", TOKEN, allow_insecure_http=True).url
        == "http://healthlog.local:3000/base"
    )
    session.post.assert_not_called()


@pytest.mark.parametrize("token", ["", "Bearer " + TOKEN, TOKEN + "\n", "password", None])
def test_bad_tokens(token):
    with pytest.raises(ValueError):
        validate_token(token)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthError),
        (403, PermissionError),
        (409, APIError),
        (422, APIError),
        (429, APIError),
        (500, APIError),
        (302, APIError),
    ],
)
async def test_errors_private_and_no_redirects(status, error, caplog):
    session, response = http_session(
        status, {"error": TOKEN + " secret biometric"}, {"Retry-After": "123"}
    )
    api = API(session, "https://healthlog.example.com/", TOKEN)
    with pytest.raises(error) as caught:
        await api.post("/api/measurements", {}, "ha:synthetic-key")
    assert caught.value.status == status
    assert caught.value.retry_after == 123
    response.json.assert_not_called()
    assert session.post.call_args.kwargs["allow_redirects"] is False
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer " + TOKEN
    assert TOKEN not in str(caught.value) + caplog.text


@pytest.mark.parametrize(
    "error", [TimeoutError("private data"), aiohttp.ClientConnectionError("private data")]
)
async def test_network_sanitized(error):
    session, _ = http_session()
    session.post.side_effect = error
    with pytest.raises(APIError) as caught:
        await API(session, "https://healthlog.example.com", TOKEN).post(
            "/api/measurements", {}, "ha:synthetic-key"
        )
    assert str(caught.value) == "HealthLog request failed (0)"


@pytest.mark.parametrize(
    "value,expected", [("20", 20), ("-1", 0), ("NaN", 0), ("inf", 0), (None, 0), ("garbage", 0)]
)
def test_retry_after(value, expected):
    assert retry_seconds(value) == expected


def test_retry_date(freezer):
    freezer.move_to("2026-01-01T00:00:00Z")
    assert retry_seconds("Thu, 01 Jan 2026 00:01:00 GMT") == 60


@pytest.mark.parametrize("metric", BATCH_TYPES)
@pytest.mark.parametrize("status", ["inserted", "duplicate"])
async def test_batch_payload_and_exact_response(metric, status):
    api = AsyncMock()
    api.post.return_value = {
        "processed": 1,
        "inserted": int(status == "inserted"),
        "duplicates": int(status == "duplicate"),
        "updated": 0,
        "failed": 0,
        "skipped": [],
        "entries": [{"index": 0, "status": status}],
    }
    item = measurement(metric)
    await Transport(api).deliver(item)
    path, body, key = api.post.call_args.args
    assert path == "/api/measurements/batch"
    assert set(body) == {"entries"}
    row = body["entries"][0]
    assert set(row) == {"hkIdentifier", "value", "unit", "startDate", "endDate", "externalId"}
    assert row["startDate"] == row["endDate"] == item["measuredAt"]
    assert key == row["externalId"] == item["externalId"]
    expected = item["value"] / 100 if metric in ("BODY_FAT", "OXYGEN_SATURATION") else item["value"]
    assert row["value"] == expected


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"entries": []},
        {"entries": [{"index": 1, "status": "inserted"}]},
        {"entries": [{"index": 0, "status": "updated"}]},
        {
            "entries": [
                {
                    "index": 0,
                    "status": "skipped",
                    "reason": "value_out_of_range",
                    "convertedValue": 9999,
                }
            ]
        },
        {"entries": [{"index": 0, "status": "failed"}]},
        {"measurements": 1, "skipped": 0},
    ],
)
async def test_not_acked_on_skips_failure_or_wrong_contract(result):
    api = AsyncMock()
    api.post.return_value = result
    with pytest.raises(APIError):
        await Transport(api).deliver(measurement())


async def test_single_preserves_notes_and_context():
    item = measurement("BLOOD_GLUCOSE")
    item.update(value=100.25, unit="mg/dL", glucoseContext="FASTING", notes="Synthetic device")
    api = AsyncMock()
    api.post.return_value = {**item, "id": "synthetic-row", "source": "EXTERNAL"}
    await Transport(api).deliver(item)
    path, body, key = api.post.call_args.args
    assert path == "/api/measurements"
    assert body == {k: item[k] for k in ("type", "value", "measuredAt", "notes", "glucoseContext")}
    assert key == item["externalId"]
    assert not durable_identity(item)


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "MANUAL"},
        {"value": 99},
        {"id": None},
        {"measuredAt": "2026-01-03T00:00:00Z"},
        {"value": True},
    ],
)
async def test_single_requires_matching_external_row(changes):
    item = measurement("FAT_MASS")
    api = AsyncMock()
    api.post.return_value = {**item, "id": "row", "source": "EXTERNAL", **changes}
    with pytest.raises(APIError):
        await Transport(api).deliver(item)


async def test_token_isolation():
    session, _ = http_session()
    await API(session, "https://a.example.com", TOKEN).post("/api/measurements", {}, "ha:test-one")
    other = "hlk_" + "b" * 64
    await API(session, "https://b.example.com", other).post("/api/measurements", {}, "ha:test-one")
    first, second = session.post.call_args_list
    assert first.kwargs["headers"]["Authorization"] == "Bearer " + TOKEN
    assert second.kwargs["headers"]["Authorization"] == "Bearer " + other


async def test_single_timestamp_server_milliseconds():
    item = measurement("FAT_MASS")
    item["measuredAt"] = "2026-01-02T03:04:05.123456+00:00"
    api = AsyncMock()
    api.post.return_value = {
        **item,
        "id": "row",
        "source": "EXTERNAL",
        "measuredAt": "2026-01-02T03:04:05.123Z",
    }
    await Transport(api).deliver(item)
    assert api.post.call_args.args[1]["measuredAt"] == item["measuredAt"]


@pytest.mark.parametrize(
    "body", [{"data": [], "error": None}, {"data": {}, "error": "private"}, ["not-envelope"]]
)
async def test_malformed_envelope(body):
    session, _ = http_session(body=body)
    with pytest.raises(APIError):
        await API(session, "https://healthlog.example.com", TOKEN).post(
            "/api/measurements", {}, "ha:synthetic-key"
        )


@pytest.mark.parametrize("metric", METRICS)
@pytest.mark.parametrize("notes", ["", "Synthetic device note"])
async def test_complete_metric_transport_matrix(metric, notes):
    unit, low, high = METRICS[metric]
    item = {**measurement(metric), "value": (low + high) / 2, "unit": unit}
    if notes:
        item["notes"] = notes
    if metric == "BLOOD_GLUCOSE":
        item["glucoseContext"] = "FASTING"
    api = AsyncMock()
    is_batch = metric in BATCH_TYPES and not notes
    api.post.return_value = (
        {"entries": [{"index": 0, "status": "inserted"}]}
        if is_batch
        else {**item, "id": "synthetic-row", "source": "EXTERNAL"}
    )
    await Transport(api).deliver(item)
    path, body, _ = api.post.call_args.args
    assert path == ("/api/measurements/batch" if is_batch else "/api/measurements")
    if not is_batch:
        assert "externalId" not in body and "unit" not in body and "source" not in body
        assert body["value"] == item["value"]
        if notes:
            assert body["notes"] == notes


@pytest.mark.parametrize("allow_http", [False, True])
async def test_invalid_certificate_no_http_fallback(
    hass, aiohttp_server, socket_enabled, tmp_path, allow_http, caplog
):
    import ssl

    from tools.e2e.run import certificates

    certificates(tmp_path / "certs")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "certs/server.pem", tmp_path / "certs/server.key")
    received = []

    async def record(request):
        received.append(request.path)
        return web.json_response({"data": {}, "error": None})

    app = web.Application()
    app.router.add_post("/api/measurements", record)
    server = await aiohttp_server(app, ssl=context)
    # This CA is not in HA's normal trusted store. Verification must fail even
    # if HTTP was allowed in configuration; no request body may reach the app.
    session = async_get_clientsession(hass)
    url = str(server.make_url("/")).replace("http://", "https://")
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    server_reset_seen = asyncio.Event()

    def expected_server_handshake_reset(loop, context):
        # The local TLS server sees the client abort its rejected handshake.
        # Only consume this expected peer-reset event, not HA/client errors.
        if (
            isinstance(context.get("exception"), (ConnectionResetError, ssl.SSLError))
            and context.get("message") == "Error on transport creation for incoming connection"
        ):
            server_reset_seen.set()
            return
        previous_handler(loop, context)

    loop.set_exception_handler(expected_server_handshake_reset)
    try:
        with pytest.raises(APIError) as caught:
            await API(session, url, TOKEN, allow_insecure_http=allow_http).post(
                "/api/measurements", {}, "ha:bad-certificate"
            )
        assert isinstance(caught.value.__context__, aiohttp.ClientConnectorCertificateError)
        await asyncio.wait_for(server_reset_seen.wait(), timeout=2)
        await server.close()
    finally:
        loop.set_exception_handler(previous_handler)
    assert caught.value.status == 0
    assert received == []
    assert TOKEN not in str(caught.value) + caplog.text


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_cross_origin_redirect_never_receives_token(
    hass, aiohttp_server, socket_enabled, status
):
    received = []

    async def target(request):
        received.append(dict(request.headers))
        return web.json_response({"data": {}, "error": None})

    sink = web.Application()
    sink.router.add_route("*", "/sink", target)
    sink_server = await aiohttp_server(sink)

    async def redirect(request):
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return web.Response(status=status, headers={"Location": str(sink_server.make_url("/sink"))})

    origin = web.Application()
    origin.router.add_post("/api/measurements", redirect)
    origin_server = await aiohttp_server(origin)
    with pytest.raises(APIError) as caught:
        await API(
            async_get_clientsession(hass),
            str(origin_server.make_url("/")),
            TOKEN,
            allow_insecure_http=True,
        ).post("/api/measurements", {}, "ha:redirect-safety")
    assert caught.value.status == status
    assert received == []


async def test_shared_session_cookie_isolation_and_path_prefix(
    hass, aiohttp_server, socket_enabled
):
    requests = []

    async def receive(request):
        requests.append(
            {
                "authorization": request.headers.get("Authorization"),
                "cookies": dict(request.cookies),
                "path": request.path,
            }
        )
        return web.json_response({"data": {}, "error": None})

    app = web.Application()
    app.router.add_post("/healthlog/api/measurements", receive)
    server = await aiohttp_server(app)
    base = URL(str(server.make_url("/healthlog"))).with_host("localhost")
    session = async_get_clientsession(hass)
    session.cookie_jar.update_cookies(
        {"healthlog_session": "other-account-session", "other_cookie": "private-cookie-value"},
        response_url=base,
    )
    first = API(session, str(base), TOKEN, allow_insecure_http=True)
    other_token = "hlk_" + "b" * 64
    second = API(session, str(base), other_token, allow_insecure_http=True)

    async def localhost_only(resolver, host, port=0, family=socket.AF_INET):
        # HA fixtures disable external DNS. Resolve only this loopback server.
        assert host == "localhost"
        return [
            {
                "hostname": host,
                "host": "127.0.0.1",
                "port": port,
                "family": socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
        ]

    with patch("aiohttp.resolver.AsyncResolver.resolve", localhost_only):
        await first.post("/api/measurements", {}, "ha:cookie-test-one")
        await second.post("/api/measurements", {}, "ha:cookie-test-two")
    assert first.session is second.session is async_get_clientsession(hass)
    assert [r["authorization"] for r in requests] == ["Bearer " + TOKEN, "Bearer " + other_token]
    assert all(r["path"] == "/healthlog/api/measurements" for r in requests)
    assert all(not value for r in requests for value in r["cookies"].values())
    # Other HA integrations retain their own shared-session cookies unchanged.
    assert (
        session.cookie_jar.filter_cookies(base)["healthlog_session"].value
        == "other-account-session"
    )
    assert "Authorization" not in session.headers
