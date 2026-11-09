"""Protect real-service harness synchronization/TLS without launching Docker."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography import x509

from tools.e2e.run import E2EFailure, Suite, certificates


def test_restart_refreshes_ephemeral_management_port():
    suite = Suite.__new__(Suite)
    suite.ha_url = "http://127.0.0.1:40001"
    suite.docker = MagicMock(return_value=MagicMock(stdout="127.0.0.1:40002\n"))
    suite.refresh_ha_address()
    assert suite.ha_url == "http://127.0.0.1:40002"
    suite.docker.assert_called_once_with("port", "homeassistant", "8123")


def test_unallocated_management_port_is_not_ready():
    suite = Suite.__new__(Suite)
    suite.docker = MagicMock(return_value=MagicMock(stdout="127.0.0.1:0\n"))
    with pytest.raises(E2EFailure, match="management port"):
        suite.refresh_ha_address()


def test_ca_and_leaf_have_strict_tls_identity_extensions(tmp_path):
    directory = tmp_path / "certs"
    certificates(directory)
    ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
    leaf = x509.load_pem_x509_certificate((directory / "server.pem").read_bytes())
    ca_ski = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
    assert ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign
    for certificate in (ca, leaf):
        assert certificate.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)
        assert (
            certificate.extensions.get_extension_for_class(
                x509.AuthorityKeyIdentifier
            ).value.key_identifier
            == ca_ski
        )
    assert leaf.issuer == ca.subject
    assert "healthlog-e2e" in leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName)


async def test_trigger_waits_for_durable_admission(freezer):
    freezer.move_to("2026-01-02T03:04:05.123456Z")
    suite = Suite.__new__(Suite)
    suite.ha = AsyncMock(return_value={"entity_id": "sensor.e2e_time"})
    suite.state = AsyncMock()
    suite.wait_queue = AsyncMock()
    profile = {"trigger": "sensor.e2e_time"}
    stamp = await suite.trigger(profile)
    suite.wait_queue.assert_awaited_once()
    predicate = suite.wait_queue.call_args.args[1]
    assert stamp == "2026-01-02T03:04:05.123+00:00"
    assert not predicate({"events": [], "seen": []})
    assert predicate({"events": [{"key": "2026-01-02T03:04:05.123000+00:00"}], "seen": []})


async def test_drained_uses_committed_queue_not_stale_entity_status():
    suite = Suite.__new__(Suite)
    suite.private = {"profiles": {"one": {"entry_id": "one"}}}
    suite.queue = MagicMock(side_effect=[{"events": [{"ready": False}]}, {"events": []}])
    suite.statuses = AsyncMock(return_value=[{"attributes": {"queued_batches": 0}}])

    async def verify(predicate, label, **kwargs):
        assert kwargs["timeout"] == 180
        assert not await predicate()
        assert await predicate()

    suite.wait = verify
    await suite.drained()
    suite.statuses.assert_not_called()
