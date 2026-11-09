"""Disposable TLS fault proxy. Never install this test service in Home Assistant.

Forwards real requests to HealthLog. Only the authenticated control API can
inject a refusal/drop; responses are otherwise the real upstream server's.
No credentials, payloads, cookies or biometric values are recorded in the journal.
"""

import asyncio
import os
import ssl

from aiohttp import ClientSession, ClientTimeout, web

CONTROL = os.environ["E2E_CONTROL_KEY"]
PLAIN_HTTP = os.environ.get("E2E_PLAIN_HTTP") == "true"
ORIGIN = "http://healthlog-http-e2e:8080" if PLAIN_HTTP else "https://healthlog-e2e:8443"


async def setup(app):
    app["session"] = ClientSession(timeout=ClientTimeout(total=90))
    app["plan"] = {}
    app["journal"] = []
    app["release"] = asyncio.Event()
    yield
    await app["session"].close()


async def control(request):
    if request.headers.get("X-E2E-Control-Key") != CONTROL:
        raise web.HTTPForbidden()
    if request.method == "POST":
        request.app["release"].set()
        request.app["release"] = asyncio.Event()
        request.app["plan"] = await request.json()
    return web.json_response({"journal": request.app["journal"], "plan": request.app["plan"]})


async def forward(request):
    upstream_path = request.path_qs
    if request.path.startswith("/healthlog/"):
        upstream_path = upstream_path.removeprefix("/healthlog")
    path = upstream_path.split("?", 1)[0]
    if path == "/_e2e/redirect-target":
        request.app["journal"].append(
            {"path": request.path, "authorization_seen": bool(request.headers.get("Authorization"))}
        )
        return web.json_response({"data": {}, "error": None})
    body = await request.read()
    metric = None
    measurement = request.method == "POST" and path in (
        "/api/measurements",
        "/api/measurements/batch",
    )
    if measurement:
        data = await request.json()
        metric = data.get("type") or data.get("entries", [{}])[0].get("hkIdentifier")
    plan = request.app["plan"]
    fault = None
    if (
        measurement
        and plan.get("remaining", 0) > 0
        and (not plan.get("metric") or plan["metric"] == metric)
    ):
        fault = plan["mode"]
        plan["remaining"] -= 1
    record = {"path": path, "request_path": request.path, "metric": metric, "fault": fault}
    if measurement:
        request.app["journal"].append(record)
    if fault == "drop_before":
        record["status"] = "dropped_before_forwarding"
        request.transport.abort()
        return web.Response()
    if fault == "hang_before":
        record["status"] = "waiting_before_forwarding"
        await request.app["release"].wait()
        if request.transport:
            request.transport.abort()
        return web.Response()
    if fault in ("rate_limit", "forbidden", "reject", "redirect"):
        status = {"rate_limit": 429, "forbidden": 403, "reject": 422, "redirect": 307}[fault]
        record["status"] = status
        return web.json_response(
            {"data": None, "error": "Synthetic E2E fault"},
            status=status,
            headers={
                "Retry-After": "3",
                "Location": plan.get("redirect_url", ORIGIN + "/_e2e/redirect-target"),
            },
        )
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in ("host", "content-length", "connection", "transfer-encoding")
    }
    headers.update(
        {"Host": ORIGIN.split("://", 1)[1], "X-Forwarded-Proto": "http" if PLAIN_HTTP else "https"}
    )
    async with request.app["session"].request(
        request.method,
        "http://healthlog:3000" + upstream_path,
        headers=headers,
        data=body,
        allow_redirects=False,
    ) as response:
        answer = await response.read()
        if measurement:
            record["status"] = response.status
        if fault == "drop_after":
            record["status"] = f"dropped_after_upstream_{response.status}"
            request.transport.abort()
            return web.Response()
        result_headers = [
            (key, value)
            for key, value in response.headers.items()
            if key.lower()
            not in ("content-length", "transfer-encoding", "connection", "content-encoding")
        ]
        return web.Response(body=answer, status=response.status, headers=result_headers)


def main():
    app = web.Application()
    app.cleanup_ctx.append(setup)
    app.router.add_route("*", "/_e2e/control", control)
    app.router.add_route("*", "/{path:.*}", forward)
    context = None
    if not PLAIN_HTTP:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain("/certs/server.pem", "/certs/server.key")
    web.run_app(
        app, host="0.0.0.0", port=8080 if PLAIN_HTTP else 8443, ssl_context=context, access_log=None
    )


if __name__ == "__main__":
    main()
