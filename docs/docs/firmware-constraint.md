# Firmware constraint: leaf-only certificates

These BMCs accept **exactly one certificate** — the leaf. This is firmware behaviour, not a tool choice, and it was verified live on 2026-08-02 against H13 firmware 01.05.09:

- The OEM `SmcSSLCert.Upload` action returns `400 GeneralError` for **any** multi-cert PEM. Both a full 4-certificate chain and a minimal leaf+intermediate bundle were rejected.
- The standard Redfish `CertificateService.ReplaceCertificate` endpoint exists, but only pairs with its own `GenerateCSR` flow — attempting to install an externally keyed certificate fails with "Certificate did not match newly generated private key". It cannot install a certificate whose key was generated elsewhere (such as by an ACME client).

Do not "fix" the leaf-only upload in the tool; the firmware will reject anything else.

## Consequences

The BMC always serves a **chainless leaf**:

- Browsers repair the chain themselves via AIA fetching, so interactive use looks fine.
- Strict TLS clients — Go, cURL, monitoring probes — fail with "unknown authority" because there is no intermediate to chain with.
- Monitoring must therefore probe these endpoints **without chain verification**, harvesting expiry rather than validating trust. (In the author's estate, a dedicated blackbox-exporter module with `insecure_skip_verify` covers the BMC fleet, while devices that serve a full chain stay on a verifying probe.)

## Vendor quirks worth knowing

- The certificate-info endpoint returns fields with vendor typos — `VaildFrom` and `GoodTHRU` — which the tool (and its tests) reproduce verbatim, because that is what the firmware sends.
- `Manager.Reset` accepts an empty POST body on some firmware generations and returns HTTP 200 **without actually restarting** (observed on H12/X12). Always name the reset type: `{"ResetType": "GracefulRestart"}`.
