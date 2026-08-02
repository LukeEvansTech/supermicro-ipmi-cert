"""Unit tests for RedfishIPMIUpdater and the fingerprint/polling helpers."""

import hashlib
import json

import supermicro_ipmi_cert as smic
from OpenSSL import crypto
from tests.conftest import FakeResponse, FakeSession, make_cert_pair

IPMI_URL = "https://bmc.test"

UPLOAD_OK_TEXT = "SSL certificate and private key were successfully uploaded"


def make_updater(responses):
    session = FakeSession(responses)
    return smic.RedfishIPMIUpdater(session, IPMI_URL), session


# ── cert_fingerprint ──────────────────────────────────────────────────────


def test_cert_fingerprint_matches_sha256_of_der(cert_pair):
    pem_cert, _ = cert_pair
    cert = crypto.load_certificate(crypto.FILETYPE_PEM, pem_cert)
    der = crypto.dump_certificate(crypto.FILETYPE_ASN1, cert)
    digest = hashlib.sha256(der).hexdigest().upper()
    expected = ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))

    assert smic.cert_fingerprint(pem_cert) == expected


def test_cert_fingerprint_uses_first_cert_of_a_bundle():
    leaf, _ = make_cert_pair(cn="leaf.test")
    intermediate, _ = make_cert_pair(cn="intermediate.test")

    assert smic.cert_fingerprint(leaf + intermediate) == smic.cert_fingerprint(leaf)


# ── upload_cert: leaf extraction is a firmware constraint ─────────────────


def test_upload_cert_sends_leaf_only(tmp_path, cert_pair):
    leaf, key = cert_pair
    intermediate, _ = make_cert_pair(cn="intermediate.test")
    dh_params = b"-----BEGIN DH PARAMETERS-----\nabc\n-----END DH PARAMETERS-----\n"
    bundle = leaf + intermediate + dh_params
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(bundle)
    key_file.write_bytes(key)

    updater, session = make_updater([FakeResponse(text=UPLOAD_OK_TEXT)])
    assert updater.upload_cert(str(key_file), str(cert_file), "token") is True

    _, url, kwargs = session.calls[0]
    assert url.endswith("SmcSSLCert.Upload")
    sent_cert = kwargs["files"]["cert_file"][1]
    assert sent_cert.count(b"BEGIN CERTIFICATE") == 1
    assert sent_cert.endswith(b"-----END CERTIFICATE-----\n")
    assert b"DH PARAMETERS" not in sent_cert
    assert smic.cert_fingerprint(sent_cert) == smic.cert_fingerprint(leaf)
    assert kwargs["files"]["key_file"][1] == key


def test_upload_cert_fails_on_firmware_rejection(tmp_path, cert_pair):
    leaf, key = cert_pair
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(leaf)
    key_file.write_bytes(key)

    updater, _ = make_updater([FakeResponse(ok=False, status_code=400, text="GeneralError")])
    assert updater.upload_cert(str(key_file), str(cert_file), "token") is False


# ── reboot_ipmi: the empty-body 200-without-restart trap ──────────────────


def test_reboot_sends_graceful_restart_body():
    updater, session = make_updater([FakeResponse()])
    assert updater.reboot_ipmi("token") is True

    _, url, kwargs = session.calls[0]
    assert url.endswith("Manager.Reset")
    assert json.loads(kwargs["data"]) == {"ResetType": "GracefulRestart"}
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["headers"]["X-Auth-Token"] == "token"


def test_reboot_failure_returns_false():
    updater, _ = make_updater([FakeResponse(ok=False, status_code=500)])
    assert updater.reboot_ipmi("token") is False


# ── get_ipmi_cert_info: vendor-typo date fields ───────────────────────────


def test_cert_info_parses_vendor_date_fields():
    updater, _ = make_updater(
        [
            FakeResponse(
                json_data={
                    "VaildFrom": "Jun  1 12:00:00 2025 GMT",
                    "GoodTHRU": "Aug 30 12:00:00 2025 GMT",
                }
            )
        ]
    )
    info = updater.get_ipmi_cert_info("token")
    assert info["has_cert"] is True
    assert (info["valid_from"].month, info["valid_from"].day) == (6, 1)
    assert (info["valid_until"].month, info["valid_until"].day) == (8, 30)
    assert info["valid_until"].year == 2025


def test_cert_info_returns_false_on_http_error():
    updater, _ = make_updater([FakeResponse(ok=False, status_code=403)])
    assert updater.get_ipmi_cert_info("token") is False


# ── wait_for_served_cert: reboot polling ──────────────────────────────────


def test_wait_retries_through_connection_errors_then_matches(monkeypatch):
    attempts = []

    def fake_served(host):
        attempts.append(host)
        if len(attempts) == 1:
            raise ConnectionResetError("BMC rebooting")
        if len(attempts) == 2:
            return "STALE"
        return "MATCH"

    monkeypatch.setattr(smic, "get_served_fingerprint", fake_served)
    monkeypatch.setattr(smic.time, "sleep", lambda _s: None)

    assert smic.wait_for_served_cert("bmc.test", "MATCH", timeout_seconds=60) is True
    assert len(attempts) == 3


def test_wait_returns_false_when_deadline_already_passed(monkeypatch):
    monkeypatch.setattr(
        smic,
        "get_served_fingerprint",
        lambda _h: (_ for _ in ()).throw(AssertionError("must not probe")),
    )
    assert smic.wait_for_served_cert("bmc.test", "MATCH", timeout_seconds=0) is False
