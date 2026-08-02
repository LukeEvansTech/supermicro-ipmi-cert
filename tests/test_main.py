"""End-to-end tests of main()'s decision flow, with the network faked out.

The scenarios mirror the production failure modes that shaped 0.2.0: a stored
certificate is not proof of a served certificate, and a reboot that fails (or
never applies the cert) must be a loud deployment failure, not a warning.
"""

import pytest
import supermicro_ipmi_cert as smic
from tests.conftest import make_cert_pair


class Recorder:
    """Patches the updater methods and module helpers; records what ran."""

    def __init__(
        self, monkeypatch, stored_valid_until_seq, served_fingerprint, reboot_ok=True, wait_ok=True
    ):
        self.calls = []
        self.stored_seq = list(stored_valid_until_seq)

        monkeypatch.setattr(
            smic.RedfishIPMIUpdater,
            "login",
            lambda _self, _u, _p: type("R", (), {"headers": {"X-Auth-Token": "tok"}})(),
        )
        monkeypatch.setattr(smic.RedfishIPMIUpdater, "get_ipmi_cert_info", self._cert_info)
        monkeypatch.setattr(smic.RedfishIPMIUpdater, "upload_cert", self._upload)
        monkeypatch.setattr(smic.RedfishIPMIUpdater, "reboot_ipmi", self._reboot)
        monkeypatch.setattr(smic, "get_served_fingerprint", self._served)
        monkeypatch.setattr(smic, "wait_for_served_cert", self._wait)
        self._served_fp = served_fingerprint
        self._reboot_ok = reboot_ok
        self._wait_ok = wait_ok

    # These are installed as class attributes while already bound to the
    # Recorder, so Python does NOT prepend the updater instance — the only
    # args received are the ones the caller passes explicitly.
    def _cert_info(self, _token):
        self.calls.append("cert_info")
        return {"has_cert": True, "valid_until": self.stored_seq.pop(0)}

    def _upload(self, _key, _cert, _token):
        self.calls.append("upload")
        return True

    def _reboot(self, _token):
        self.calls.append("reboot")
        return self._reboot_ok

    def _served(self, _host):
        self.calls.append("served_probe")
        return self._served_fp

    def _wait(self, _host, _fp, **_kw):
        self.calls.append("wait")
        return self._wait_ok


@pytest.fixture
def cert_files(tmp_path):
    pem_cert, pem_key = make_cert_pair()
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(pem_cert)
    key_file.write_bytes(pem_key)
    return cert_file, key_file, pem_cert


def run_main(monkeypatch, cert_file, key_file):
    argv = [
        "supermicro_ipmi_cert.py",
        "--ipmi-url",
        "https://bmc.test",
        "--model",
        "H13",
        "--key-file",
        str(key_file),
        "--cert-file",
        str(cert_file),
        "--username",
        "admin",
        "--password",
        "secret",
        "--quiet",
    ]
    monkeypatch.setattr("sys.argv", argv)
    return smic.main()


def test_nothing_to_do_requires_served_match(monkeypatch, cert_files):
    cert_file, key_file, pem_cert = cert_files
    stored = smic.parse_valid_until(str(cert_file))
    rec = Recorder(
        monkeypatch,
        stored_valid_until_seq=[stored],
        served_fingerprint=smic.cert_fingerprint(pem_cert),
    )

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, cert_file, key_file)

    assert exc.value.code == 0
    assert "upload" not in rec.calls
    assert "reboot" not in rec.calls
    assert "served_probe" in rec.calls


def test_stored_current_but_served_stale_reboots_without_upload(monkeypatch, cert_files):
    cert_file, key_file, _pem_cert = cert_files
    stored = smic.parse_valid_until(str(cert_file))
    rec = Recorder(
        monkeypatch,
        stored_valid_until_seq=[stored],
        served_fingerprint="AA:BB:STALE",
    )

    run_main(monkeypatch, cert_file, key_file)

    assert "upload" not in rec.calls
    assert "reboot" in rec.calls
    assert "wait" in rec.calls


def test_new_cert_uploads_reboots_and_verifies(monkeypatch, cert_files):
    cert_file, key_file, _pem_cert = cert_files
    new_valid_until = smic.parse_valid_until(str(cert_file))
    old_stored = new_valid_until.replace(year=new_valid_until.year - 1)
    rec = Recorder(
        monkeypatch,
        stored_valid_until_seq=[old_stored, new_valid_until],
        served_fingerprint="ignored",
    )

    run_main(monkeypatch, cert_file, key_file)

    assert rec.calls.index("upload") < rec.calls.index("reboot") < rec.calls.index("wait")


def test_upload_not_reflected_by_bmc_is_failure(monkeypatch, cert_files):
    cert_file, key_file, _pem_cert = cert_files
    new_valid_until = smic.parse_valid_until(str(cert_file))
    old_stored = new_valid_until.replace(year=new_valid_until.year - 1)
    Recorder(
        monkeypatch,
        stored_valid_until_seq=[old_stored, old_stored],
        served_fingerprint="ignored",
    )

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, cert_file, key_file)
    assert exc.value.code == 2


def test_reboot_failure_is_deployment_failure(monkeypatch, cert_files):
    cert_file, key_file, _pem_cert = cert_files
    new_valid_until = smic.parse_valid_until(str(cert_file))
    old_stored = new_valid_until.replace(year=new_valid_until.year - 1)
    Recorder(
        monkeypatch,
        stored_valid_until_seq=[old_stored, new_valid_until],
        served_fingerprint="ignored",
        reboot_ok=False,
    )

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, cert_file, key_file)
    assert exc.value.code == 2


def test_served_cert_never_matching_after_reboot_is_failure(monkeypatch, cert_files):
    cert_file, key_file, _pem_cert = cert_files
    new_valid_until = smic.parse_valid_until(str(cert_file))
    old_stored = new_valid_until.replace(year=new_valid_until.year - 1)
    Recorder(
        monkeypatch,
        stored_valid_until_seq=[old_stored, new_valid_until],
        served_fingerprint="ignored",
        wait_ok=False,
    )

    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, cert_file, key_file)
    assert exc.value.code == 2


def test_no_reboot_flag_skips_reboot_and_wait(monkeypatch, cert_files):
    cert_file, key_file, _pem_cert = cert_files
    new_valid_until = smic.parse_valid_until(str(cert_file))
    old_stored = new_valid_until.replace(year=new_valid_until.year - 1)
    rec = Recorder(
        monkeypatch,
        stored_valid_until_seq=[old_stored, new_valid_until],
        served_fingerprint="ignored",
    )

    argv_extra = ["--no-reboot"]
    monkeypatch.setattr(
        "sys.argv",
        [
            "supermicro_ipmi_cert.py",
            "--ipmi-url",
            "https://bmc.test",
            "--model",
            "H13",
            "--key-file",
            str(key_file),
            "--cert-file",
            str(cert_file),
            "--username",
            "admin",
            "--password",
            "secret",
            "--quiet",
        ]
        + argv_extra,
    )
    smic.main()

    assert "upload" in rec.calls
    assert "reboot" not in rec.calls
    assert "wait" not in rec.calls
