#!/usr/bin/env python3

"""
Supermicro IPMI Certificate Updater (Redfish API)
Supports X12, X13, and H13 motherboards using Redfish API

This is a simplified version that only supports modern Supermicro boards
with Redfish API. Legacy X9/X10/X11 support has been removed.

Copyright (c) Jari Turkia (original)
Modified for Redfish-only support
"""

import argparse
import json
import logging
import os
import re
import ssl
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

REQUEST_TIMEOUT = 30.0
# The SmcSSLCert.Upload action can take well over 30s on some BMC generations
# (H12/X12 observed): the BMC validates and stores the key material before
# responding, and may bounce its web backend as part of it.
UPLOAD_TIMEOUT = 180.0


class RedfishIPMIUpdater:
    """IPMI certificate updater for Redfish-based Supermicro boards (X12/X13/H13)"""

    def __init__(self, session, ipmi_url):
        self.session = session
        self.ipmi_url = ipmi_url.rstrip("/")

        # Redfish API endpoints
        self.login_url = f"{ipmi_url}/redfish/v1/SessionService/Sessions"
        self.cert_info_url = f"{ipmi_url}/redfish/v1/UpdateService/Oem/Supermicro/SSLCert"
        self.upload_cert_url = (
            f"{ipmi_url}/redfish/v1/UpdateService/Oem/Supermicro/SSLCert/Actions/SmcSSLCert.Upload"
        )
        self.reboot_url = f"{ipmi_url}/redfish/v1/Managers/1/Actions/Manager.Reset"

        error_log = logging.getLogger("RedfishIPMIUpdater")
        error_log.setLevel(logging.ERROR)
        self.setLogger(error_log)

    def setLogger(self, logger):
        self.logger = logger

    def login(self, username, password):
        """
        Log into IPMI using Redfish API
        :param username: IPMI username
        :param password: IPMI password
        :return: response object or False
        """
        print(f"DEBUG: Logging in via Redfish to {self.login_url}")

        login_data = {"UserName": username, "Password": password}

        request_headers = {"Content-Type": "application/json"}

        try:
            result = self.session.post(
                self.login_url,
                data=json.dumps(login_data),
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
                verify=False,
            )
        except Exception as e:
            print(f"ERROR: Connection error during login: {e}")
            return False

        if not result.ok:
            print(f"ERROR: Login failed with status code: {result.status_code}")
            print(f"ERROR: Response: {result.text}")
            return False

        print("DEBUG: Login successful, got auth token")
        return result

    def get_ipmi_cert_info(self, token):
        """
        Get current certificate information from IPMI
        :param token: X-Auth-Token from login
        :return: dict with certificate info or False
        """
        request_headers = {"Content-Type": "application/json", "X-Auth-Token": token}

        try:
            r = self.session.get(
                self.cert_info_url, headers=request_headers, verify=False, timeout=REQUEST_TIMEOUT
            )
        except Exception as e:
            print(f"ERROR: Error getting cert info: {e}")
            return False

        if not r.ok:
            print(f"ERROR: Failed to get cert info: {r.status_code}")
            return False

        try:
            data = r.json()
            # Parse dates - Supermicro format includes timezone that needs to be stripped
            valid_from_str = data["VaildFrom"].rstrip(re.split(r"\d{4}", data["VaildFrom"])[1])
            valid_until_str = data["GoodTHRU"].rstrip(re.split(r"\d{4}", data["GoodTHRU"])[1])

            valid_from = datetime.strptime(valid_from_str, r"%b %d %H:%M:%S %Y")
            valid_until = datetime.strptime(valid_until_str, r"%b %d %H:%M:%S %Y")

            return {"has_cert": True, "valid_from": valid_from, "valid_until": valid_until}
        except Exception as e:
            print(f"ERROR: Error parsing cert info: {e}")
            self.logger.error(f"Error parsing cert info: {e}")
            return False

    def upload_cert(self, key_file, cert_file, token):
        """
        Upload certificate to IPMI via Redfish
        :param key_file: path to private key file
        :param cert_file: path to certificate file
        :param token: X-Auth-Token from login
        :return: bool
        """
        print(f"DEBUG: Reading certificate from {cert_file}")
        print(f"DEBUG: Reading key from {key_file}")

        with open(key_file, "rb") as fh:
            key_data = fh.read()

        with open(cert_file, "rb") as fh:
            cert_data = fh.read()
            # Extract certificates only (IPMI doesn't like DH PARAMS)
            cert_data = (
                b"\n".join(
                    re.findall(
                        b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
                        cert_data,
                        re.DOTALL,
                    )
                )
                + b"\n"
            )

        # Leaf only - a FIRMWARE constraint, not a choice (do not "fix" this).
        # Tested live on H13 fw 01.05.09 (2026-08-02): SmcSSLCert.Upload
        # returns 400 GeneralError for any multi-cert PEM (4-cert bundle AND
        # a minimal leaf+intermediate), and the standard Redfish
        # CertificateService.ReplaceCertificate only pairs with its own
        # GenerateCSR flow ("Certificate did not match newly generated
        # private key") so it cannot install an externally keyed cert either.
        # Consequence: these BMCs always serve a chainless leaf - browsers
        # repair that via AIA fetching, but strict TLS clients fail with
        # "unknown authority", so monitoring must probe them without chain
        # verification.
        substr = b"-----END CERTIFICATE-----\n"
        cert_only = cert_data.split(substr, maxsplit=1)[0] + substr

        print(f"DEBUG: Certificate data length: {len(cert_data)} bytes")
        print(f"DEBUG: Server cert only length: {len(cert_only)} bytes")
        print(f"DEBUG: Key data length: {len(key_data)} bytes")

        # Use dict format for multipart file upload
        files_to_upload = {
            "cert_file": ("cert.pem", cert_only, "application/octet-stream"),
            "key_file": ("key.pem", key_data, "application/octet-stream"),
        }

        request_headers = {"X-Auth-Token": token}

        print(f"DEBUG: Uploading to {self.upload_cert_url}")
        try:
            result = self.session.post(
                self.upload_cert_url,
                files=files_to_upload,
                headers=request_headers,
                timeout=UPLOAD_TIMEOUT,
                verify=False,
            )
        except Exception as e:
            print(f"ERROR: Upload error: {e}")
            return False

        print(f"DEBUG: Upload response status: {result.status_code}")
        print(f"DEBUG: Upload response text: {result.text}")
        self.logger.debug(f"Upload response status: {result.status_code}")
        self.logger.debug(f"Upload response text: {result.text}")

        if "SSL certificate and private key were successfully uploaded" not in result.text:
            print(f"ERROR: Upload failed. Status: {result.status_code}")
            print(f"ERROR: Response: {result.text}")
            print(f"ERROR: Response headers: {result.headers}")
            return False

        print("SUCCESS: Certificate uploaded successfully!")
        return True

    def reboot_ipmi(self, token):
        """
        Reboot IPMI to apply certificate changes
        :param token: X-Auth-Token from login
        :return: bool
        """
        request_headers = {"Content-Type": "application/json", "X-Auth-Token": token}

        # An empty POST body is accepted by some firmware but silently ignored by
        # others (seen on H12/X12: HTTP 200, no actual restart), which leaves the
        # old certificate being served. Always name the reset type.
        reboot_data = {"ResetType": "GracefulRestart"}

        try:
            result = self.session.post(
                self.reboot_url,
                data=json.dumps(reboot_data),
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
                verify=False,
            )
        except Exception as e:
            print(f"ERROR: Reboot error: {e}")
            return False

        if not result.ok:
            print(f"ERROR: Reboot failed: {result.status_code}")
            return False

        return True


def parse_valid_until(pem_file):
    """Parse certificate expiration date from PEM file"""
    from OpenSSL import crypto as c

    with open(pem_file, "rb") as fh:
        cert = c.load_certificate(c.FILETYPE_PEM, fh.read())
    return datetime.strptime(cert.get_notAfter().decode("utf8"), "%Y%m%d%H%M%SZ")


def cert_fingerprint(pem_data):
    """SHA-256 fingerprint of the first (leaf) certificate in a PEM blob"""
    from OpenSSL import crypto as c

    cert = c.load_certificate(c.FILETYPE_PEM, pem_data)
    return cert.digest("sha256").decode("utf8")


def get_served_fingerprint(host, port=443):
    """Fingerprint of the certificate the host is actually serving on its TLS port"""
    pem = ssl.get_server_certificate((host, port), timeout=REQUEST_TIMEOUT)
    return cert_fingerprint(pem.encode("utf8"))


def wait_for_served_cert(host, expected_fingerprint, timeout_seconds=240, interval=10):
    """
    Poll host:443 until the served certificate matches the expected fingerprint.
    The IPMI drops TLS entirely while rebooting, so connection errors are expected.
    :return: bool
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            if get_served_fingerprint(host) == expected_fingerprint:
                return True
            print("DEBUG: Served certificate does not match uploaded certificate yet")
        except Exception as e:
            print(f"DEBUG: TLS probe failed ({e}), IPMI likely still rebooting")
        time.sleep(interval)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Update Supermicro IPMI SSL certificate (Redfish API only)"
    )
    parser.add_argument("--ipmi-url", required=True, help="Supermicro IPMI URL")
    parser.add_argument(
        "--model", required=True, help="Board model: X12, X13, or H13 (all use Redfish)"
    )
    parser.add_argument("--key-file", required=True, help="X.509 Private key filename")
    parser.add_argument("--cert-file", required=True, help="X.509 Certificate filename")
    parser.add_argument("--username", required=True, help="IPMI username with admin access")
    parser.add_argument("--password", required=True, help="IPMI user password")
    parser.add_argument(
        "--no-reboot", action="store_true", help="Skip IPMI reboot (manual reboot required)"
    )
    parser.add_argument(
        "--force-update", action="store_true", help="Force update even if certificate dates match"
    )
    parser.add_argument("--quiet", action="store_true", help="Minimal output")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Validate files exist
    if not os.path.isfile(args.key_file):
        print(f"ERROR: --key-file '{args.key_file}' doesn't exist!")
        exit(2)
    if not os.path.isfile(args.cert_file):
        print(f"ERROR: --cert-file '{args.cert_file}' doesn't exist!")
        exit(2)

    # Normalize URL
    if args.ipmi_url.endswith("/"):
        args.ipmi_url = args.ipmi_url[:-1]

    # Validate model
    if args.model.upper() not in ["X12", "X13", "H13"]:
        print(f"ERROR: Unsupported model '{args.model}'")
        print("This version only supports X12, X13, and H13 boards with Redfish API")
        exit(2)

    # Normalize X13 and H13 to X12 (they use the same API)
    model_display = args.model.upper()
    if args.model.upper() in ["X13", "H13"] and not args.quiet:
        print(f"Note: {args.model.upper()} uses same Redfish API as X12")

    # Enable debug logging if requested
    if args.debug:
        import http.client as http_client

        http_client.HTTPConnection.debuglevel = 1

        logging.basicConfig()
        logging.getLogger().setLevel(logging.DEBUG)
        requests_log = logging.getLogger("requests.packages.urllib3")
        requests_log.setLevel(logging.DEBUG)
        requests_log.propagate = True

    # Disable SSL warnings (IPMI certs are often self-signed)
    requests.packages.urllib3.disable_warnings(
        requests.packages.urllib3.exceptions.InsecureRequestWarning
    )

    # Create updater
    if not args.quiet:
        print(f"Board model is {model_display}")

    session = requests.session()
    updater = RedfishIPMIUpdater(session, args.ipmi_url)

    if args.debug:
        debug_log = logging.getLogger("RedfishIPMIUpdater")
        debug_log.setLevel(logging.DEBUG)
        updater.setLogger(debug_log)

    # Login
    login_response = updater.login(args.username, args.password)
    if not login_response:
        print("ERROR: Login failed. Cannot continue!")
        exit(2)

    try:
        token = login_response.headers["X-Auth-Token"]
    except Exception as e:
        print(f"ERROR: Failed to get auth token: {e}")
        exit(2)

    # Get current certificate info
    cert_info = updater.get_ipmi_cert_info(token)
    if not cert_info:
        print("ERROR: Failed to get certificate information from IPMI!")
        exit(2)

    current_valid_until = cert_info.get("valid_until", None)
    if not args.quiet and cert_info["has_cert"]:
        print(f"There exists a certificate, which is valid until: {cert_info['valid_until']}")

    # Check if update is needed
    new_valid_until = parse_valid_until(args.cert_file)
    with open(args.cert_file, "rb") as fh:
        new_fingerprint = cert_fingerprint(fh.read())
    ipmi_host = urlparse(args.ipmi_url).hostname

    needs_upload = True
    if current_valid_until == new_valid_until:
        if args.force_update:
            print("New cert validity period matches existing cert, will update regardless")
        else:
            # A matching stored certificate does not prove it is being served: an
            # earlier upload whose reboot never took effect leaves the old
            # certificate live. Only the socket is authoritative.
            try:
                served_matches = get_served_fingerprint(ipmi_host) == new_fingerprint
            except Exception as e:
                print(f"WARNING: Could not read served certificate: {e}")
                served_matches = False
            if served_matches:
                print("New cert matches existing cert and it is being served, nothing to do")
                exit(0)
            print("Stored certificate is current but the served certificate is stale!")
            print("Skipping upload, rebooting IPMI to apply the stored certificate")
            needs_upload = False

    # Upload certificate
    if needs_upload:
        if not updater.upload_cert(args.key_file, args.cert_file, token):
            print("ERROR: Failed to upload certificate to IPMI!")
            exit(2)

        if not args.quiet:
            print("Uploaded files ok.")

        # Verify the IPMI now reports the uploaded certificate
        cert_info = updater.get_ipmi_cert_info(token)
        if not cert_info:
            print("ERROR: Failed to verify certificate after upload!")
            exit(2)

        if not args.quiet and cert_info["has_cert"]:
            print(f"After upload, certificate is valid until: {cert_info['valid_until']}")

        if cert_info.get("valid_until") != new_valid_until:
            print("ERROR: IPMI does not report the uploaded certificate after upload!")
            exit(2)

    # Reboot to apply
    if args.no_reboot:
        if not args.quiet:
            print("Skipping reboot (manual reboot required)")
            print("NOTE: the new certificate is NOT served until the IPMI reboots")
    else:
        if not args.quiet:
            print("Rebooting IPMI to apply changes...")
        # An uploaded-but-unapplied certificate looks like success everywhere
        # except the socket, so a failed reboot is a deployment failure - exit
        # non-zero so the caller (Cert Warden post processing) records it and
        # the deployment can be retried.
        if not updater.reboot_ipmi(token):
            print("ERROR: IPMI reboot failed - uploaded certificate is not being served!")
            exit(2)

        if not args.quiet:
            print("Waiting for IPMI to come back serving the new certificate...")
        if not wait_for_served_cert(ipmi_host, new_fingerprint):
            print("ERROR: IPMI did not serve the uploaded certificate after reboot!")
            exit(2)
        if not args.quiet:
            print("Verified: IPMI is serving the uploaded certificate")

    if not args.quiet:
        print("All done!")


if __name__ == "__main__":
    main()
