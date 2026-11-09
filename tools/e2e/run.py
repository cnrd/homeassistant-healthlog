"""Real-container HA -> HealthLog E2E suite; no production credentials needed.

Run with `uv run python tools/e2e/run.py`. Docker Compose is required.
All credentials, TLS keys and instance data live in a new private temp directory.
Only this run's randomly named Compose project is stopped/removed on exit.
"""

import argparse
import asyncio
import ipaddress
import json
import math
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from custom_components.healthlog.models import METRICS  # noqa: E402

HA_IMAGE = "ghcr.io/home-assistant/home-assistant:2026.11.0"
HL_IMAGE = "ghcr.io/mbombeck/healthlog:v1.47.1"
DB_IMAGE = "postgres:18.6-alpine"
SERVER_URL = "https://healthlog-e2e:8443"


class E2EFailure(Exception):
    """Only nonsensitive failure descriptions may appear in reports."""


def require(condition, message):
    if not condition:
        raise E2EFailure(message)


def command(args, *, check=True, input=None):
    result = subprocess.run(args, input=input, text=True, capture_output=True)
    if check and result.returncode:
        # Do not echo command arguments: a provisioning command may contain secrets.
        raise E2EFailure(f"Container command failed (exit {result.returncode})")
    return result


def private_json(path, data):
    path.write_text(json.dumps(data, indent=2))
    path.chmod(0o600)


def certificates(directory):
    directory.mkdir()
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "HealthLog disposable E2E CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "healthlog-e2e")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("healthlog-e2e"),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    (directory / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (directory / "server.pem").write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    (directory / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (directory / "server.key").chmod(0o600)


class Suite:
    def __init__(self, workdir, *, resume=False):
        self.workdir = workdir
        self.started = time.monotonic()
        self.results = []
        if resume:
            self.private = json.loads((workdir / "private.json").read_text())
        else:
            workdir.mkdir(mode=0o700, parents=True, exist_ok=False)
            self.private = {
                "project": "healthlog-e2e-" + secrets.token_hex(4),
                "db_password": secrets.token_hex(24),
                "encryption": secrets.token_hex(32),
                "hmac": secrets.token_hex(32),
                "control": secrets.token_hex(32),
                "profiles": {},
            }
            self.prepare()
        self.compose = [
            "docker",
            "compose",
            "-p",
            self.private["project"],
            "-f",
            str(workdir / "compose.json"),
        ]
        self.ha_url = None
        self.hl_url = None
        self.session = None

    def save(self):
        private_json(self.workdir / "private.json", self.private)

    def prepare(self):
        certificates(self.workdir / "certs")
        config = self.workdir / "ha"
        config.mkdir()
        (config / "configuration.yaml").write_text(
            "homeassistant:\n  name: HealthLog E2E\n  country: DK\n  time_zone: UTC\n"
            "  latitude: 0\n  longitude: 0\n  elevation: 0\n  unit_system: metric\n"
            "http:\napi:\nconfig:\nfrontend:\nonboarding:\n"
            "logger:\n  default: warning\n"
        )
        services = {
            "db": {
                "image": DB_IMAGE,
                "environment": {
                    "POSTGRES_USER": "healthlog",
                    "POSTGRES_DB": "healthlog",
                    "POSTGRES_PASSWORD": self.private["db_password"],
                },
                "volumes": ["database:/var/lib/postgresql"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U healthlog"],
                    "interval": "2s",
                    "timeout": "3s",
                    "retries": 30,
                },
            },
            "healthlog": {
                "image": HL_IMAGE,
                "environment": {
                    "DATABASE_URL": "postgresql://healthlog:"
                    + self.private["db_password"]
                    + "@db:5432/healthlog?schema=public&connection_limit=10",
                    "NODE_ENV": "production",
                    "ENCRYPTION_KEY": self.private["encryption"],
                    "API_TOKEN_HMAC_KEY": self.private["hmac"],
                    "APP_URL": SERVER_URL,
                    "NEXT_PUBLIC_APP_URL": SERVER_URL,
                    "SESSION_COOKIE_SECURE": "true",
                    "HEALTHLOG_PROCESS_TYPE": "web",
                    "DB_BACKUP_ENABLED": "false",
                    "DB_BACKUP_BEFORE_MIGRATE": "false",
                    "IP_GEO_LOOKUP_DISABLED": "1",
                    "PASSWORD_BREACH_CHECK_DISABLED": "1",
                    "UPDATE_CHECK_DISABLED": "1",
                    "ENVIRONMENT_AIR_QUALITY_DISABLED": "1",
                    "LOG_LEVEL": "error",
                    "LOG_INCLUDE_STACK": "false",
                },
                "depends_on": {"db": {"condition": "service_healthy"}},
            },
            "proxy": {
                "image": HA_IMAGE,
                "entrypoint": ["python3", "/suite/proxy.py"],
                "volumes": [
                    f"{ROOT / 'tools/e2e'}:/suite:ro",
                    f"{self.workdir / 'certs'}:/certs:ro",
                ],
                "environment": {"E2E_CONTROL_KEY": self.private["control"]},
                "ports": ["127.0.0.1::8443"],
                "networks": {"default": {"aliases": ["healthlog-e2e"]}, "control": {}},
            },
            "plain-proxy": {
                "image": HA_IMAGE,
                "entrypoint": ["python3", "/suite/proxy.py"],
                "volumes": [f"{ROOT / 'tools/e2e'}:/suite:ro"],
                "environment": {
                    "E2E_CONTROL_KEY": self.private["control"],
                    "E2E_PLAIN_HTTP": "true",
                },
                "ports": ["127.0.0.1::8080"],
                "networks": {"default": {"aliases": ["healthlog-http-e2e"]}, "control": {}},
            },
            "homeassistant": {
                "image": HA_IMAGE,
                "entrypoint": ["python3", "-m", "homeassistant", "--config", "/config"],
                "environment": {"REQUESTS_CA_BUNDLE": "/certs/ca.pem"},
                "volumes": [
                    f"{config}:/config",
                    f"{ROOT / 'custom_components/healthlog'}:/config/custom_components/healthlog:ro",
                    f"{self.workdir / 'certs'}:/certs:ro",
                ],
                "ports": ["127.0.0.1::8123"],
                "networks": {"default": {}, "control": {}},
            },
        }
        private_json(
            self.workdir / "compose.json",
            {
                "services": services,
                "volumes": {"database": {}},
                "networks": {"default": {"internal": True}, "control": {}},
            },
        )
        self.save()

    def docker(self, *args, **kwargs):
        return command([*self.compose, *args], **kwargs)

    def sql(self, query):
        return self.docker(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "healthlog",
            "-d",
            "healthlog",
            "-At",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            query,
        ).stdout.strip()

    def rows(self, user_id=None):
        where = "WHERE deleted_at IS NULL"
        if user_id:
            where += " AND user_id='" + user_id.replace("'", "''") + "'"
        return json.loads(
            self.sql(
                "SELECT coalesce(json_agg(row_to_json(m)), '[]') FROM ("
                "SELECT id,user_id,type,value,unit,source,measured_at AT TIME ZONE 'UTC' AS measured_at,external_id,glucose_context,ingest_token_id "
                f"FROM measurements {where} ORDER BY created_at,id) m"
            )
        )

    async def request(self, method, url, *, expected=(200, 201), **kwargs):
        async with self.session.request(method, url, allow_redirects=False, **kwargs) as response:
            try:
                data = await response.json()
            except ValueError, aiohttp.ContentTypeError:
                data = None
            if response.status not in expected:
                raise E2EFailure(
                    f"{method} {url.split('?', 1)[0].split('/')[-1]} returned HTTP {response.status}"
                )
            return data

    async def ha(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = "Bearer " + self.private["ha_access"]
        return await self.request(method, self.ha_url + path, headers=headers, **kwargs)

    async def control(self, plan=None, *, plain=False):
        return await self.request(
            "GET" if plan is None else "POST",
            (self.http_url if plain else self.hl_url) + "/_e2e/control",
            headers={"X-E2E-Control-Key": self.private["control"]},
            **({} if plan is None else {"json": plan}),
        )

    async def wait(self, predicate, label, timeout=180):  # noqa: ASYNC109
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = await predicate()
            if result:
                return result
            await asyncio.sleep(0.5)
        raise E2EFailure(f"Timed out: {label}")

    async def ready(self, url, *, budget=600):
        async def probe():
            try:
                async with self.session.get(url, allow_redirects=False) as response:
                    return response.status == 200
            except aiohttp.ClientConnectorCertificateError:
                raise E2EFailure("Disposable server TLS certificate validation failed") from None
            except aiohttp.ClientError, TimeoutError:
                return False

        await self.wait(probe, "service readiness", timeout=budget)

    async def boot(self):
        print("Starting isolated Home Assistant, HealthLog, PostgreSQL and TLS proxy", flush=True)
        result = await asyncio.to_thread(self.docker, "up", "-d", check=False)
        if result.returncode:
            (self.workdir / "startup.log").write_text(result.stderr)
            raise E2EFailure("Compose startup failed; private startup.log retained")
        ha_port = self.docker("port", "homeassistant", "8123").stdout.strip().rsplit(":", 1)[1]
        hl_port = self.docker("port", "proxy", "8443").stdout.strip().rsplit(":", 1)[1]
        http_port = self.docker("port", "plain-proxy", "8080").stdout.strip().rsplit(":", 1)[1]
        require(
            int(ha_port) > 0 and int(hl_port) > 0, "Docker did not allocate loopback test ports"
        )
        self.ha_url, self.hl_url = f"http://127.0.0.1:{ha_port}", f"https://localhost:{hl_port}"
        self.http_url = f"http://127.0.0.1:{http_port}"
        context = ssl.create_default_context(cafile=str(self.workdir / "certs/ca.pem"))
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=context), timeout=aiohttp.ClientTimeout(total=100)
        )
        await self.ready(self.ha_url + "/api/onboarding")
        await self.ready(self.hl_url + "/api/version")
        version = await self.request("GET", self.hl_url + "/api/version")
        self.image_digests = {
            name: json.loads(
                command(
                    ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"]
                ).stdout
            )
            for name, image in (
                ("homeassistant", HA_IMAGE),
                ("healthlog", HL_IMAGE),
                ("postgres", DB_IMAGE),
            )
        }
        self.results.append(
            {
                "check": "real_services_started",
                "result": "passed",
                "healthlog_version_response": version,
            }
        )

    async def bootstrap(self):
        if "ha_access" not in self.private:
            client = self.ha_url + "/"
            response = await self.request(
                "POST",
                self.ha_url + "/api/onboarding/users",
                json={
                    "name": "Disposable E2E",
                    "username": "e2e",
                    "password": secrets.token_urlsafe(32),
                    "client_id": client,
                    "language": "en",
                },
            )
            response = await self.request(
                "POST",
                self.ha_url + "/auth/token",
                data={
                    "grant_type": "authorization_code",
                    "code": response["auth_code"],
                    "client_id": client,
                },
            )
            self.private.update(
                ha_access=response["access_token"], ha_refresh=response["refresh_token"]
            )
            self.save()
        if "accounts" not in self.private:
            accounts = []
            for name in ("alpha", "beta"):
                password = secrets.token_urlsafe(36)
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(
                        ssl=ssl.create_default_context(cafile=str(self.workdir / "certs/ca.pem"))
                    ),
                    cookie_jar=aiohttp.CookieJar(unsafe=True),
                ) as client:
                    response = await client.post(
                        self.hl_url + "/api/auth/register",
                        json={
                            "email": f"{name}@e2e.invalid",
                            "username": "e2e_" + name,
                            "password": password,
                            "timezone": "UTC",
                        },
                        headers={"Origin": SERVER_URL},
                    )
                    require(
                        response.status in (200, 201),
                        f"Test-account registration failed: HTTP {response.status}",
                    )
                    await response.read()
                    response = await client.post(
                        self.hl_url + "/api/tokens/measurements",
                        json={
                            "name": "Disposable HA E2E",
                            "expiresInDays": 1,
                        },
                        headers={"Origin": SERVER_URL},
                    )
                    require(
                        response.status == 201,
                        f"Restricted token mint failed: HTTP {response.status}",
                    )
                    body = await response.json()
                    token = body["data"]["token"]
                user_id = self.sql(f"SELECT id FROM users WHERE username='e2e_{name}'")
                accounts.append(
                    {"name": name, "user_id": user_id, "token": token, "password": password}
                )
            self.private["accounts"] = accounts
            self.save()
        require(self.rows() == [], "Fresh test database unexpectedly contains measurements")
        print("Provisioned two isolated accounts and restricted measurement tokens", flush=True)

    async def state(self, entity_id, value, attributes=None):
        return await self.ha(
            "POST",
            "/api/states/" + entity_id,
            json={"state": str(value), "attributes": attributes or {}},
        )

    async def profile(
        self,
        name,
        mappings,
        *,
        account=0,
        notes="",
        settling=2,
        server_url=SERVER_URL,
        allow_http=False,
    ):
        if name in self.private["profiles"]:
            return self.private["profiles"][name]
        trigger = "sensor.e2e_" + name + "_time"
        await self.state(trigger, "2000-01-01T00:00:00+00:00")
        connections = self.private.setdefault("connections", {})
        connection_key = f"{account}:{server_url}:{allow_http}"
        entry_id = connections.get(connection_key)
        if entry_id is None:
            flow = await self.ha(
                "POST",
                "/api/config/config_entries/flow",
                json={"handler": "healthlog"},
            )
            if server_url.startswith("http://") and allow_http:
                flow = await self.ha(
                    "POST",
                    "/api/config/config_entries/flow/" + flow["flow_id"],
                    json={"url": server_url, "token": self.private["accounts"][account]["token"]},
                )
                require(
                    flow.get("errors", {}).get("url") == "invalid_url",
                    "HTTP setup did not require explicit opt-in",
                )
            flow = await self.ha(
                "POST",
                "/api/config/config_entries/flow/" + flow["flow_id"],
                json={
                    "url": server_url,
                    "token": self.private["accounts"][account]["token"],
                    "allow_insecure_http": allow_http,
                },
            )
            require(flow.get("type") == "create_entry", "Connection flow did not create a parent")
            entry_id = connections[connection_key] = flow["result"]["entry_id"]
            require(
                flow.get("next_flow", [None])[0] == "config_subentries_flow",
                "Initial setup did not continue with a profile subentry",
            )
            flow = await self.ha(
                "GET", "/api/config/config_entries/subentries/flow/" + flow["next_flow"][1]
            )
        else:
            flow = await self.ha(
                "POST",
                "/api/config/config_entries/subentries/flow",
                json={"handler": [entry_id, "profile"]},
            )
        require(flow.get("step_id") == "profile", "Subentry flow did not reach profile editor")
        options = {
            "name": name,
            "trigger_entity": trigger,
            "trigger_attribute": "",
            "trigger_mode": "timestamp",
            "mappings": [
                {key: value for key, value in mapping.items() if key != "glucose_context"}
                for mapping in mappings
            ],
            "description": notes,
            "settling_delay": settling,
            "freshness_window": 120,
            "batch_delay": 1,
            "queue_capacity": 100,
        }
        flow = await self.ha(
            "POST", "/api/config/config_entries/subentries/flow/" + flow["flow_id"], json=options
        )
        glucose = next(
            (mapping for mapping in mappings if mapping["type"] == "BLOOD_GLUCOSE"), None
        )
        if glucose is not None:
            require(
                flow.get("step_id") == "glucose",
                "Glucose context step was not shown for the glucose mapping",
            )
            flow = await self.ha(
                "POST",
                "/api/config/config_entries/subentries/flow/" + flow["flow_id"],
                json={"glucose_context": glucose["glucose_context"]},
            )
        require(flow.get("type") == "create_entry", "Profile flow did not create an entry")
        profile_id = flow["unique_id"]
        subentries = await self.websocket(
            {"type": "config_entries/subentries/list", "entry_id": entry_id}
        )
        subentry_id = next(
            item["subentry_id"] for item in subentries if item["unique_id"] == profile_id
        )
        profile = {
            "entry_id": entry_id,
            "subentry_id": subentry_id,
            "profile_id": profile_id,
            "trigger": trigger,
            "options": options,
            "account": account,
        }
        self.private["profiles"][name] = profile
        self.save()

        async def loaded():
            entries = await self.ha("GET", "/api/config/config_entries/entry?domain=healthlog")
            parent_loaded = any(
                entry["entry_id"] == entry_id and entry["state"] == "loaded" for entry in entries
            )
            entities = await self.websocket({"type": "config/entity_registry/list"})
            return (
                parent_loaded
                and len(
                    [
                        entity
                        for entity in entities
                        if entity.get("config_subentry_id") == subentry_id
                    ]
                )
                == 2
            )

        await self.wait(loaded, "profile setup", timeout=60)
        return profile

    async def trigger(self, profile):
        existing = await self.ha("GET", "/api/states/" + profile["trigger"], expected=(200, 404))
        if not existing.get("entity_id"):
            await self.state(profile["trigger"], "2000-01-01T00:00:00+00:00")
            await asyncio.sleep(0.1)
        stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        await self.state(profile["trigger"], stamp)
        event_key = datetime.fromisoformat(stamp).isoformat()
        await self.wait_queue(
            profile,
            lambda queue: (
                event_key in queue["seen"]
                or any(event["key"] == event_key for event in queue["events"])
            ),
            "trigger capture durably admitted",
            timeout=15,
        )
        return stamp

    async def statuses(self):
        states = await self.ha("GET", "/api/states")
        return [
            state
            for state in states
            if state["entity_id"].startswith("sensor.") and "queued_batches" in state["attributes"]
        ]

    async def drained(self, *, timeout=180):  # noqa: ASYNC109
        async def empty():
            queues = [self.queue(profile) for profile in self.private["profiles"].values()]
            return queues and all(queue is not None and not queue["events"] for queue in queues)

        await self.wait(empty, "all delivery queues drain", timeout=timeout)

    def queue(self, profile):
        path = (
            self.workdir
            / "ha/.storage"
            / f"healthlog.{profile['entry_id']}.{profile['profile_id']}.queue"
        )
        return json.loads(path.read_text())["data"] if path.exists() else None

    async def wait_queue(self, profile, predicate, label, timeout=180):  # noqa: ASYNC109
        async def matches():
            queue = self.queue(profile)
            return queue is not None and predicate(queue)

        await self.wait(matches, label, timeout=timeout)

    async def websocket(self, message):
        async with self.session.ws_connect(self.ha_url + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.private["ha_access"]})
            require(
                (await ws.receive_json())["type"] == "auth_ok", "HA websocket authentication failed"
            )
            await ws.send_json({"id": 1, **message})
            result = await ws.receive_json()
            require(result.get("success"), "HA websocket operation failed")
            return result["result"]

    async def retry(self, profile):
        registry = await self.websocket({"type": "config/entity_registry/list"})
        button = next(
            item["entity_id"]
            for item in registry
            if item["config_entry_id"] == profile["entry_id"]
            and item.get("config_subentry_id") == profile["subentry_id"]
            and item["entity_id"].startswith("button.")
        )
        await self.ha("POST", "/api/services/button/press", json={"entity_id": button})

    async def restart_ha(self):
        await asyncio.to_thread(self.docker, "restart", "homeassistant")
        self.refresh_ha_address()
        await self.ready(self.ha_url + "/api/onboarding", budget=90)

        async def loaded():
            entries = await self.ha("GET", "/api/config/config_entries/entry?domain=healthlog")
            return entries and all(entry["state"] == "loaded" for entry in entries)

        await self.wait(loaded, "entries reload after real HA process restart", timeout=90)

    def refresh_ha_address(self):
        # Docker can allocate a different ephemeral host port on each start.
        # A stale management URL must not be mistaken for failed HA recovery.
        port = self.docker("port", "homeassistant", "8123").stdout.strip().rsplit(":", 1)[1]
        require(int(port) > 0, "Docker did not allocate the restarted HA management port")
        self.ha_url = f"http://127.0.0.1:{port}"

    async def options(self, profile, step, data):
        flow = await self.ha(
            "POST",
            "/api/config/config_entries/subentries/flow",
            json={
                "handler": [profile["entry_id"], "profile"],
                "subentry_id": profile["subentry_id"],
            },
        )
        flow = await self.ha(
            "POST",
            "/api/config/config_entries/subentries/flow/" + flow["flow_id"],
            json={"next_step_id": step},
        )
        require(flow.get("step_id") == step, "Options menu did not open expected step")
        return await self.ha(
            "POST", "/api/config/config_entries/subentries/flow/" + flow["flow_id"], json=data
        )

    async def delete_profile(self, profile):
        await self.websocket(
            {
                "type": "config_entries/subentries/delete",
                "entry_id": profile["entry_id"],
                "subentry_id": profile["subentry_id"],
            }
        )
        await self.wait(lambda: self.profile_removed(profile), "profile queue deletion", timeout=20)
        self.private["profiles"] = {
            name: data
            for name, data in self.private["profiles"].items()
            if data["subentry_id"] != profile["subentry_id"]
        }
        self.save()

    async def profile_removed(self, profile):
        return self.queue(profile) is None

    async def check(self, name, operation):
        started = time.monotonic()
        await operation()
        self.results.append(
            {"check": name, "result": "passed", "seconds": round(time.monotonic() - started, 2)}
        )
        print("PASS " + name, flush=True)

    async def metric_matrix(self):
        mappings, attributes = [], {}
        for metric, (unit, low, high) in METRICS.items():
            attributes[metric.lower()] = (low + high) / 2
            mapping = {
                "type": metric,
                "entity_id": "sensor.e2e_matrix",
                "attribute": metric.lower(),
                "unit": unit,
            }
            if metric == "BLOOD_GLUCOSE":
                mapping["glucose_context"] = "FASTING"
            mappings.append(mapping)
        await self.state("sensor.e2e_matrix", "ready", attributes)
        journal_before = len((await self.control())["journal"])
        profile = await self.profile("matrix", mappings)
        require(not self.rows(), "Setup manufactured measurements")
        require(
            len((await self.control())["journal"]) == journal_before,
            "Setup performed a measurement write",
        )
        stamp = await self.trigger(profile)
        await self.drained(timeout=150)
        rows = self.rows()
        require(len(rows) == 25, "Metric matrix did not produce exactly 25 records")
        require({row["type"] for row in rows} == set(METRICS), "Stored metric coverage differs")
        for row in rows:
            metric = row["type"]
            require(
                math.isclose(float(row["value"]), attributes[metric.lower()], rel_tol=1e-12),
                f"Stored value mismatch: {metric}",
            )
            require(row["unit"] == METRICS[metric][0], f"Stored unit mismatch: {metric}")
            require(
                datetime.fromisoformat(row["measured_at"]) == datetime.fromisoformat(stamp),
                f"Stored timestamp mismatch: {metric}",
            )
            require(
                row["source"] == "EXTERNAL" and row["ingest_token_id"],
                "Incorrect source/token attribution",
            )
        require(
            next(row for row in rows if row["type"] == "BLOOD_GLUCOSE")["glucose_context"]
            == "FASTING",
            "Glucose context was not preserved",
        )
        self.matrix_profile = profile
        self.matrix_attributes = attributes

    async def identical_reading_new_event(self):
        before = len(self.rows())
        await self.state("sensor.e2e_matrix", "ready", self.matrix_attributes)
        stamp = await self.trigger(self.matrix_profile)
        await self.drained(timeout=150)
        require(
            len(self.rows()) == before + 25, "Identical readings from a distinct event were lost"
        )
        await self.state(self.matrix_profile["trigger"], stamp, {"irrelevant": "updated"})
        await asyncio.sleep(4)
        require(
            len(self.rows()) == before + 25, "Duplicate trigger identity created another record"
        )

    async def state_template_isolation(self):
        before = len(self.rows())
        await self.state("sensor.e2e_pounds", "132.27735731092655", {"unit_of_measurement": "lb"})
        await self.state("sensor.e2e_mass_g", "60000")
        await self.state("sensor.e2e_extra", "0.25")
        profile = await self.profile(
            "beta",
            [
                {"type": "WEIGHT", "entity_id": "sensor.e2e_pounds"},
                {
                    "type": "LEAN_BODY_MASS",
                    "unit": "kg",
                    "value_template": "{{ states('sensor.e2e_mass_g') | float / 1000 + states('sensor.e2e_extra') | float }}",
                },
            ],
            account=1,
            settling=4,
        )
        stamp = await self.trigger(profile)
        await asyncio.sleep(1)
        await self.state("sensor.e2e_extra", "0.5")
        await self.drained(timeout=30)
        rows = self.rows(self.private["accounts"][1]["user_id"])
        require(len(self.rows()) == before + 2, "Profile delivery count differs")
        require(len(rows) == 2, "Cross-account delivery or missing second-account measurements")
        require(
            math.isclose(
                next(row for row in rows if row["type"] == "WEIGHT")["value"], 60, rel_tol=1e-12
            ),
            "Entity-state pound conversion differs",
        )
        require(
            next(row for row in rows if row["type"] == "LEAN_BODY_MASS")["value"] == 60.5,
            "Late Jinja dependency was not captured",
        )
        require(
            all(
                datetime.fromisoformat(row["measured_at"]) == datetime.fromisoformat(stamp)
                for row in rows
            ),
            "Second-account timestamp differs",
        )

    async def subentry_lifecycle(self):
        before = len(self.rows())
        await self.state("sensor.e2e_subentry_weight", "68.125", {"unit_of_measurement": "kg"})
        primary = await self.profile(
            "subentry_weight",
            [{"type": "WEIGHT", "entity_id": "sensor.e2e_subentry_weight"}],
            settling=8,
        )
        await self.trigger(primary)
        original = self.queue(primary)["events"][0]
        require(not original["ready"], "Primary capture was not pending")
        await self.state("sensor.e2e_subentry_pulse", "75", {"unit_of_measurement": "bpm"})
        sibling = await self.profile(
            "subentry_pulse", [{"type": "PULSE", "entity_id": "sensor.e2e_subentry_pulse"}]
        )
        require(
            primary["entry_id"] == sibling["entry_id"] == self.matrix_profile["entry_id"],
            "Different entities required separate connections",
        )
        result = await self.options(sibling, "profile", {**sibling["options"], "batch_delay": 2})
        require(
            result.get("type") == "abort" and result.get("reason") == "reconfigure_successful",
            "Profile reconfiguration failed",
        )
        pending = self.queue(primary)["events"][0]
        require(
            not pending["ready"] and pending["due"] == original["due"],
            "Adding/editing a sibling interrupted settling",
        )
        await self.wait_queue(
            primary, lambda q: not q["events"], "primary settling completes normally", timeout=20
        )
        await self.trigger(sibling)
        await self.wait_queue(
            sibling, lambda q: not q["events"], "edited sibling delivers", timeout=20
        )
        require(len(self.rows()) == before + 2, "Profile delivery count differs")
        await self.delete_profile(sibling)
        require(self.queue(primary) is not None, "Removing a profile deleted its sibling queue")
        await self.trigger(primary)
        await self.wait_queue(
            primary,
            lambda q: not q["events"],
            "sibling continues after profile deletion",
            timeout=20,
        )
        require(len(self.rows()) == before + 3, "Profile deletion disrupted sibling collection")
        entries = await self.ha("GET", "/api/config/config_entries/entry?domain=healthlog")
        require(len(entries) == 2, "Multiple entity profiles created extra account connections")

    async def notes_preserved(self):
        before = len(self.rows())
        await self.state("sensor.e2e_notes", "61.123456", {"unit_of_measurement": "kg"})
        profile = await self.profile(
            "notes",
            [{"type": "WEIGHT", "entity_id": "sensor.e2e_notes"}],
            notes="Synthetic E2E device note",
        )
        await self.trigger(profile)
        await self.drained(timeout=20)
        require(len(self.rows()) == before + 1, "Notes measurement missing or duplicated")
        require(self.rows()[-1]["external_id"] is None, "Notes unexpectedly used the batch route")
        count = self.sql(
            "SELECT count(*) FROM measurements WHERE notes_encrypted IS NOT NULL AND notes IS NULL"
        )
        require(int(count) == 1, "Real server did not store encrypted notes")

    async def pending_restart(self):
        before = len(self.rows())
        await self.state("sensor.e2e_pending", "62.125", {"unit_of_measurement": "kg"})
        profile = await self.profile(
            "pending", [{"type": "WEIGHT", "entity_id": "sensor.e2e_pending"}], settling=30
        )
        stamp = await self.trigger(profile)
        await self.wait_queue(
            profile,
            lambda q: q["events"] and not q["events"][0]["ready"],
            "pending snapshot durably stored",
            timeout=10,
        )
        await self.restart_ha()
        await self.wait_queue(
            profile,
            lambda q: not q["events"],
            "pending capture recovered and delivered",
            timeout=30,
        )
        rows = self.rows()
        require(len(rows) == before + 1, "Pending restart lost or duplicated a reading")
        require(rows[-1]["value"] == 62.125, "Pending restart reread unrelated live state")
        require(
            datetime.fromisoformat(rows[-1]["measured_at"]) == datetime.fromisoformat(stamp),
            "Pending restart changed original timestamp",
        )

    async def frozen_cancel_restart(self):
        before = len(self.rows())
        await self.state("sensor.e2e_frozen", "63.25", {"unit_of_measurement": "kg"})
        profile = await self.profile(
            "frozen", [{"type": "WEIGHT", "entity_id": "sensor.e2e_frozen"}]
        )
        await self.control(
            {"mode": "hang_before", "metric": "HKQuantityTypeIdentifierBodyMass", "remaining": 1}
        )
        stamp = await self.trigger(profile)

        async def in_flight():
            return any(
                row["fault"] == "hang_before" and row.get("status") == "waiting_before_forwarding"
                for row in (await self.control())["journal"]
            )

        await self.wait(in_flight, "frozen request reached proxy", timeout=20)
        queue = self.queue(profile)
        require(
            queue["events"][0]["ready"] and queue["events"][0]["payload"][0]["inflight"],
            "Frozen delivery intent was not durable before HTTP",
        )
        await asyncio.to_thread(self.docker, "stop", "homeassistant")
        await self.control({})
        await asyncio.to_thread(self.docker, "start", "homeassistant")
        self.refresh_ha_address()
        await self.ready(self.ha_url + "/api/onboarding", budget=90)
        await self.wait_queue(
            profile, lambda q: not q["events"], "frozen delivery recovered", timeout=60
        )
        require(len(self.rows()) == before + 1, "Cancelled frozen delivery was lost or duplicated")
        row = self.rows()[-1]
        require(
            row["value"] == 63.25
            and datetime.fromisoformat(row["measured_at"]) == datetime.fromisoformat(stamp),
            "Frozen snapshot changed during restart",
        )

    async def ambiguous_and_batch_retry(self):
        """Use a real two-minute backoff; expire only disposable server replay cells."""
        before = len(self.rows())
        cases = [
            ("uncertain_before", "FAT_MASS", "kg", 12.125, "drop_before", "FAT_MASS"),
            ("uncertain_after", "MUSCLE_MASS", "kg", 32.125, "drop_after", "MUSCLE_MASS"),
            (
                "batch_after",
                "WEIGHT",
                "kg",
                64.125,
                "drop_after",
                "HKQuantityTypeIdentifierBodyMass",
            ),
        ]
        profiles = []
        for name, metric, unit, value, mode, wire_metric in cases:
            entity = "sensor.e2e_" + name
            await self.state(entity, value, {"unit_of_measurement": unit})
            profile = await self.profile(name, [{"type": metric, "entity_id": entity}])
            await self.control({"mode": mode, "metric": wire_metric, "remaining": 1})
            await self.trigger(profile)
            if metric == "WEIGHT":
                await self.wait_queue(
                    profile,
                    lambda q: q["failures"] == 1,
                    "batch lost-response retry scheduled",
                    timeout=20,
                )
            else:
                await self.wait_queue(
                    profile,
                    lambda q: (
                        q["events"]
                        and q["events"][0].get("payload", [{}])[0].get("hold") == "ambiguous"
                    ),
                    "ordinary write held as ambiguous",
                    timeout=20,
                )
                await self.retry(profile)
                require(
                    self.queue(profile)["events"][0]["payload"][0].get("hold") == "ambiguous",
                    "Retry released an uncertain ordinary write",
                )
            profiles.append(profile)
        await self.control({})
        require(
            len(self.rows()) == before + 2,
            "Timeout before/after commit produced wrong server record count",
        )
        # Removing only the test server's response cache proves replay cannot
        # hide a duplicate write. No 24-hour wall-clock wait or runtime patch.
        self.sql("DELETE FROM idempotency_keys")
        await self.restart_ha()
        print(
            "Waiting through real persisted backoff to verify durable batch deduplication",
            flush=True,
        )
        await self.wait_queue(
            profiles[2],
            lambda q: not q["events"],
            "batch retry after server replay-cache removal",
            timeout=150,
        )
        require(len(self.rows()) == before + 2, "Retry duplicated a committed server measurement")
        for profile in profiles[:2]:
            require(
                self.queue(profile)["events"][0]["payload"][0]["hold"] == "ambiguous",
                "Restart or backoff released an uncertain delivery",
            )
        # Reconcile a proven non-commit and a proven commit through actual HA UI flows.
        for profile, resolution in zip(profiles[:2], ("not_delivered", "delivered"), strict=True):
            item = self.queue(profile)["events"][0]["payload"][0]
            result = await self.options(
                profile,
                "resolve",
                {"measurement": item["externalId"], "resolution": resolution, "confirmed": True},
            )
            require(
                result.get("type") == "abort" and result.get("reason") == "resolved",
                "Reconciliation did not finish",
            )
            await self.wait_queue(
                profile, lambda q: not q["events"], "reconciled queue drains", timeout=30
            )
        require(
            len(self.rows()) == before + 3,
            "Explicit reconciliation lost or duplicated a measurement",
        )

    async def rate_limit_and_rejection(self):
        before = len(self.rows())
        profiles = []
        for name, metric, value, mode, wire_metric in (
            ("rate", "WEIGHT", 65.125, "rate_limit", "HKQuantityTypeIdentifierBodyMass"),
            ("reject", "FAT_MASS", 13.125, "reject", "FAT_MASS"),
        ):
            entity = "sensor.e2e_" + name
            await self.state(entity, value, {"unit_of_measurement": "kg"})
            profile = await self.profile(name, [{"type": metric, "entity_id": entity}])
            await self.control({"mode": mode, "metric": wire_metric, "remaining": 1})
            await self.trigger(profile)
            await self.wait_queue(
                profile, lambda q: q["failures"] == 1, "server refusal persisted", timeout=20
            )
            if mode == "rate_limit":
                require(
                    self.queue(profile)["next_attempt"] > time.time() + 100,
                    "Persisted rate-limit backoff was not honored",
                )
            else:
                require(
                    self.queue(profile)["events"][0]["payload"][0].get("hold") == "rejected",
                    "422 was not quarantined",
                )
            await self.retry(profile)
            profiles.append(profile)
        await self.control({})
        await asyncio.sleep(5)
        require(len(self.rows()) == before, "Retry button bypassed backoff")
        print("Waiting through real 429/rejection backoff", flush=True)
        for profile in profiles:
            await self.wait_queue(
                profile, lambda q: not q["events"], "refused delivery safely retried", timeout=150
            )
        require(len(self.rows()) == before + 2, "Refused delivery recovery count differs")

    async def authentication_replacement(self):
        before = len(self.rows())
        account = self.private["accounts"][1]
        profile = self.private["profiles"]["beta"]
        await self.state("sensor.e2e_beta_pulse", "75", {"unit_of_measurement": "bpm"})
        sibling = await self.profile(
            "beta_pulse", [{"type": "PULSE", "entity_id": "sensor.e2e_beta_pulse"}], account=1
        )
        require(
            sibling["entry_id"] == profile["entry_id"],
            "Same-account profiles did not share a connection",
        )
        self.sql("UPDATE api_tokens SET revoked=true WHERE user_id='" + account["user_id"] + "'")
        await self.state("sensor.e2e_pounds", "132.27735731092655", {"unit_of_measurement": "lb"})
        await self.state("sensor.e2e_mass_g", "60000")
        await self.state("sensor.e2e_extra", "0.5")
        stamp = await self.trigger(profile)
        await self.wait_queue(
            profile,
            lambda q: q["error"] == "reauth_required",
            "real revoked token produces 401/reauth",
            timeout=20,
        )
        require(len(self.rows()) == before, "Revoked token unexpectedly wrote a measurement")
        await self.trigger(sibling)

        async def reauth_flow():
            flows = await self.websocket({"type": "config_entries/flow/progress"})
            return next(
                (
                    item
                    for item in flows
                    if item.get("context", {}).get("entry_id") == profile["entry_id"]
                    and item["context"].get("source") == "reauth"
                ),
                None,
            )

        flow = await self.wait(reauth_flow, "reauthentication flow registered", timeout=20)
        context = ssl.create_default_context(cafile=str(self.workdir / "certs/ca.pem"))
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=context), cookie_jar=aiohttp.CookieJar(unsafe=True)
        ) as client:
            async with client.post(
                self.hl_url + "/api/auth/login",
                json={"email": f"{account['name']}@e2e.invalid", "password": account["password"]},
                headers={"Origin": SERVER_URL},
            ) as response:
                require(response.status == 200, "Disposable verifier login failed")
                await response.read()
            async with client.post(
                self.hl_url + "/api/tokens/measurements",
                json={"name": "Replacement E2E measurement token", "expiresInDays": 1},
                headers={"Origin": SERVER_URL},
            ) as response:
                require(response.status == 201, "Replacement restricted token mint failed")
                token = (await response.json())["data"]["token"]
        account["token"] = token
        self.save()
        result = await self.ha(
            "POST",
            "/api/config/config_entries/flow/" + flow["flow_id"],
            json={"url": SERVER_URL, "token": token},
        )
        require(
            result.get("type") == "abort" and result.get("reason") == "reauth_successful",
            "HA reauthentication flow did not finish",
        )
        await self.wait_queue(
            profile,
            lambda q: not q["events"],
            "reauthenticated frozen measurements delivered",
            timeout=30,
        )
        await self.wait_queue(
            sibling,
            lambda q: not q["events"],
            "sibling resumes after parent token replacement",
            timeout=30,
        )
        require(
            len(self.rows()) == before + 3,
            "Token replacement lost or duplicated frozen measurements",
        )
        new_rows = [
            row
            for row in self.rows(account["user_id"])
            if datetime.fromisoformat(row["measured_at"]) == datetime.fromisoformat(stamp)
        ]
        require(
            len(new_rows) == 2,
            "Token replacement changed original measurement timestamps or account",
        )

    async def forbidden_and_redirect(self):
        before = len(self.rows())
        profiles = []
        for name, mode in (("permission", "forbidden"), ("redirect", "redirect")):
            entity = "sensor.e2e_" + name
            await self.state(entity, "14.125", {"unit_of_measurement": "kg"})
            profile = await self.profile(name, [{"type": "FAT_MASS", "entity_id": entity}])
            await self.control(
                {
                    "mode": mode,
                    "metric": "FAT_MASS",
                    "remaining": 1,
                    "redirect_url": "http://healthlog-http-e2e:8080/_e2e/redirect-target",
                }
            )
            await self.trigger(profile)
            await self.wait_queue(
                profile,
                lambda q: q.get("paused"),
                "permanent connection refusal pauses delivery",
                timeout=20,
            )
            queue = self.queue(profile)
            if mode == "forbidden":
                require(
                    queue["error"] == "http_403", "403 was misclassified as invalid credentials"
                )
                require(
                    not queue["events"][0]["payload"][0].get("hold"),
                    "Explicit permission refusal was treated as committed",
                )
            else:
                require(
                    queue["events"][0]["payload"][0].get("hold") == "ambiguous",
                    "Ordinary redirect was not safely held",
                )
            profiles.append(profile)
        await self.control({})
        await asyncio.sleep(4)
        require(len(self.rows()) == before, "403/redirect manufactured a server measurement")
        require(
            not any(
                row["path"] == "/_e2e/redirect-target" for row in (await self.control())["journal"]
            ),
            "Credentials followed an HTTP redirect",
        )
        require(
            not any(
                row["path"] == "/_e2e/redirect-target"
                for row in (await self.control(plain=True))["journal"]
            ),
            "Credentials followed a cross-origin HTTPS-to-HTTP redirect",
        )
        # Delete only these profiles, never their shared parent/siblings.
        for profile in profiles:
            await self.delete_profile(profile)

    async def transport_policy(self):
        before = len(self.rows())
        for name, url, allow in (
            ("https_prefix", "healthlog-e2e:8443/healthlog/", False),
            ("lan_http", "http://healthlog-http-e2e:8080/healthlog/", True),
        ):
            entity = "sensor.e2e_" + name
            await self.state(entity, "66.125", {"unit_of_measurement": "kg"})
            profile = await self.profile(
                name, [{"type": "WEIGHT", "entity_id": entity}], server_url=url, allow_http=allow
            )
            await self.trigger(profile)
            await self.wait_queue(
                profile, lambda q: not q["events"], "prefix/transport delivery", timeout=20
            )
        require(
            len(self.rows()) == before + 2,
            "HTTPS default/path-prefix or explicit LAN HTTP delivery failed",
        )
        for plain in (False, True):
            require(
                any(
                    row.get("request_path") == "/healthlog/api/measurements/batch"
                    and row.get("status") == 200
                    for row in (await self.control(plain=plain))["journal"]
                ),
                "Configured path prefix was not preserved",
            )
        # A trusted test CA does not make a certificate for another hostname
        # valid. HTTP opt-in must neither disable verification nor trigger fallback.
        await self.state("sensor.e2e_bad_cert", "67.125", {"unit_of_measurement": "kg"})
        profile = await self.profile(
            "bad_cert",
            [{"type": "WEIGHT", "entity_id": "sensor.e2e_bad_cert"}],
            server_url="https://proxy:8443",
            allow_http=True,
        )
        await self.trigger(profile)
        await self.wait_queue(
            profile, lambda q: q["failures"] == 1, "invalid HTTPS certificate refused", timeout=20
        )
        require(
            self.queue(profile)["error"] == "cannot_connect",
            "Invalid HTTPS certificate was not refused",
        )
        require(
            len(self.rows()) == before + 2,
            "Invalid certificate caused a fallback or measurement write",
        )
        await self.delete_profile(profile)

    async def run(self, *, configuration_smoke=False):
        await self.boot()
        await self.bootstrap()
        await self.check("all_25_metrics_real_server_contract", self.metric_matrix)
        if configuration_smoke:
            await self.check(
                "state_units_jinja_settling_and_account_isolation", self.state_template_isolation
            )
            await self.check(
                "shared_connection_subentries_add_edit_delete_and_settling", self.subentry_lifecycle
            )
            return
        await self.check(
            "identical_readings_distinct_events_and_duplicate_trigger",
            self.identical_reading_new_event,
        )
        await self.check(
            "state_units_jinja_settling_and_account_isolation", self.state_template_isolation
        )
        await self.check("notes_preserved_via_ordinary_route", self.notes_preserved)
        await self.check(
            "shared_connection_subentries_add_edit_delete_and_settling", self.subentry_lifecycle
        )
        await self.check("pending_capture_survives_real_restart", self.pending_restart)
        await self.check(
            "frozen_snapshot_and_inflight_cancellation_recovery", self.frozen_cancel_restart
        )
        await self.check(
            "https_defaults_http_opt_in_path_prefix_and_invalid_certificate", self.transport_policy
        )
        await self.check(
            "lost_responses_durable_batch_dedup_and_explicit_reconciliation",
            self.ambiguous_and_batch_retry,
        )
        await self.check(
            "429_backoff_422_quarantine_and_retry_button", self.rate_limit_and_rejection
        )
        await self.check(
            "real_revoked_token_401_and_reauthentication", self.authentication_replacement
        )
        await self.check(
            "403_classification_redirect_refusal_and_entry_cleanup", self.forbidden_and_redirect
        )

    async def close(self, keep=False):
        if self.session:
            await self.session.close()
        if not keep:
            await asyncio.to_thread(
                self.docker, "down", "--volumes", "--remove-orphans", check=False
            )


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--resume", type=Path)
    parser.add_argument(
        "--keep", action="store_true", help="Keep this run's containers/volume for debugging"
    )
    parser.add_argument(
        "--configuration-smoke",
        action="store_true",
        help="Test real setup with/without glucose and delivery; skip failure/backoff scenarios",
    )
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    args.report = args.report or ROOT / "docs" / (
        "CONFIG_FLOW_E2E_RESULTS.json" if args.configuration_smoke else "E2E_RESULTS.json"
    )
    workdir = (
        args.resume
        or args.workdir
        or Path(tempfile.gettempdir()) / ("healthlog-e2e-" + secrets.token_hex(6))
    )
    suite = Suite(workdir, resume=args.resume is not None)
    failed = False
    try:
        await suite.run(configuration_smoke=args.configuration_smoke)
    except (Exception, asyncio.CancelledError) as error:
        failed = True
        message = str(error) if isinstance(error, E2EFailure) else type(error).__name__
        suite.results.append({"check": "suite", "result": "failed", "message": message})
        print("FAIL " + message, flush=True)
        logs = suite.docker("logs", "--no-color", check=False)
        text = logs.stdout + logs.stderr
        for secret in (
            suite.private.get("db_password"),
            suite.private.get("encryption"),
            suite.private.get("hmac"),
            suite.private.get("control"),
            suite.private.get("ha_access"),
            suite.private.get("ha_refresh"),
        ):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        for account in suite.private.get("accounts", []):
            text = text.replace(account["token"], "[REDACTED]").replace(
                account["password"], "[REDACTED]"
            )
        (workdir / "diagnostic.log").write_text(text)
    finally:
        report = {
            "scope": "Home Assistant -> HealthLog only; Apple Health excluded",
            "synthetic_data_only": True,
            "mode": "configuration_smoke" if args.configuration_smoke else "full",
            "images": {"homeassistant": HA_IMAGE, "healthlog": HL_IMAGE, "postgres": DB_IMAGE},
            "image_digests": getattr(suite, "image_digests", {}),
            "executed_at": datetime.now(UTC).isoformat(),
            "duration_seconds": round(time.monotonic() - suite.started, 2),
            "results": suite.results,
            "passed": not failed,
            "containers_retained": args.keep,
        }
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        await suite.close(keep=args.keep)
    print(f"Report: {args.report}", flush=True)
    print(f"Private disposable workspace: {workdir}", flush=True)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
