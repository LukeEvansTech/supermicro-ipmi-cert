"""Shared test helpers: throwaway self-signed certs and fake HTTP plumbing."""

import pytest
from OpenSSL import crypto


def make_cert_pair(days=90, cn="bmc.test"):
    """Generate a self-signed cert + key, returned as PEM bytes."""
    key = crypto.PKey()
    key.generate_key(crypto.TYPE_RSA, 2048)
    cert = crypto.X509()
    cert.get_subject().CN = cn
    cert.set_serial_number(1)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(days * 86400)
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(key)
    cert.sign(key, "sha256")
    pem_cert = crypto.dump_certificate(crypto.FILETYPE_PEM, cert)
    pem_key = crypto.dump_privatekey(crypto.FILETYPE_PEM, key)
    return pem_cert, pem_key


class FakeResponse:
    def __init__(self, ok=True, status_code=200, text="", json_data=None, headers=None):
        self.ok = ok
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}

    def json(self):
        return self._json_data


class FakeSession:
    """Records every get/post and replays canned responses in order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def _next(self):
        if not self.responses:
            raise AssertionError("FakeSession ran out of canned responses")
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self._next()

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self._next()


@pytest.fixture
def cert_pair():
    return make_cert_pair()
