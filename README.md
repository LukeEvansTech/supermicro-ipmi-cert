# supermicro-ipmi-cert

Deploys TLS certificates to Supermicro BMCs (X12 / X13 / H13 generations) over the Redfish API, and — critically — verifies that the BMC is _actually serving_ the uploaded certificate before reporting success.

Runs as a container spawned by [Cert Warden](https://www.certwarden.com/) post-processing: Cert Warden renews a Let's Encrypt certificate, a deploy Job runs this image, and the BMC comes back serving the new cert with no human involved.

## Why verification is the whole point

Uploading a certificate to a Supermicro BMC is not the same as the BMC serving it. Two silent failure modes were observed in production (both fixed in 0.2.0):

1. **The reboot that never happened.** `Manager.Reset` with an empty POST body returns HTTP 200 on H12/X12 firmware without actually restarting — the old certificate stays live. The tool always sends `{"ResetType": "GracefulRestart"}` and treats a failed reboot as a deployment failure (exit 2).
2. **Stored ≠ served.** The BMC can report the new certificate as installed while the socket still serves the old one. After reboot, the tool polls `:443` until the served leaf matches the uploaded certificate's SHA-256 fingerprint, and fails if it never does. The "nothing to do" fast path is also socket-verified: if the stored cert is current but the served one is stale, the tool reboots the BMC to self-heal.

One BMC served an expired certificate for 3.5 months while every deploy log said success — hence: **trust the socket, not the API.**

## Firmware constraint: leaf-only certificates

These BMCs accept **exactly one certificate** — the leaf. This is firmware behaviour, not a tool choice (verified live 2026-08-02 on H13 firmware 01.05.09):

- The OEM `SmcSSLCert.Upload` action returns `400 GeneralError` for any multi-cert PEM (tested with a full chain and with a minimal leaf+intermediate).
- The standard Redfish `CertificateService.ReplaceCertificate` only pairs with its own `GenerateCSR` flow, so it cannot install an externally keyed certificate either.

Consequence: the BMC always serves a chainless leaf. Browsers repair the chain via AIA fetching; strict TLS clients (Go, cURL, monitoring probes) fail with "unknown authority" and must skip chain verification when probing these endpoints.

## Usage

```console
python3 supermicro_ipmi_cert.py \
  --ipmi-url https://bmc.example.com \
  --model H13 \
  --key-file privkey.pem \
  --cert-file cert.pem \
  --username ADMIN \
  --password '...'
```

| Flag                         | Description                                                       |
| ---------------------------- | ----------------------------------------------------------------- |
| `--ipmi-url`                 | BMC base URL                                                      |
| `--model`                    | `X12`, `X13`, or `H13` (all share the same Redfish API)           |
| `--key-file` / `--cert-file` | PEM private key and certificate (leaf is extracted automatically) |
| `--username` / `--password`  | BMC account with admin access                                     |
| `--no-reboot`                | Upload only; the new cert is NOT served until the BMC reboots     |
| `--force-update`             | Upload even if the stored certificate dates already match         |
| `--quiet` / `--debug`        | Output control                                                    |

Exit code 0 means the BMC is serving the uploaded certificate (or genuinely had nothing to do); exit 2 means it is not — the caller should record a failed deployment and retry.

## Deployment context

The image is published as `ghcr.io/lukeevanstech/supermicro-ipmi-cert` and consumed by the Cert Warden deploy Jobs in [talos-cluster](https://github.com/LukeEvansTech/talos-cluster) (`kubernetes/apps/infrastructure/certwarden/cert-deployment/supermicro/`). The wrapper there reads BMC credentials from a Kubernetes secret using the `kubectl` bundled in this image, then invokes the tool with the flags above.

Timeouts are tuned to real hardware: `SmcSSLCert.Upload` can take well over 30s on H12/X12 (180s allowed), and the post-reboot poll allows 240s for the BMC web server to return.

## History

This tool lived in the [containers](https://github.com/LukeEvansTech/containers) monorepo (`apps/supermicro-ipmi-cert`) until 2026-08-02; history before then is in that repository. Based on [Jari Turkia's ipmi-updater.py](https://github.com/jturkia/supermicro-ipmi-updater), reduced to Redfish-only boards and extended with served-certificate verification.

## License

GPL-2.0 (inherited from the original ipmi-updater.py).
