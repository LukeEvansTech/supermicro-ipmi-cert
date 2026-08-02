# Usage

```console
python3 supermicro_ipmi_cert.py \
  --ipmi-url https://bmc.example.com \
  --model H13 \
  --key-file privkey.pem \
  --cert-file cert.pem \
  --username ADMIN \
  --password '...'
```

## Flags

| Flag                         | Description                                                       |
| ---------------------------- | ----------------------------------------------------------------- |
| `--ipmi-url`                 | BMC base URL                                                      |
| `--model`                    | `X12`, `X13`, or `H13` (all share the same Redfish API)           |
| `--key-file` / `--cert-file` | PEM private key and certificate (leaf is extracted automatically) |
| `--username` / `--password`  | BMC account with admin access                                     |
| `--no-reboot`                | Upload only; the new cert is NOT served until the BMC reboots     |
| `--force-update`             | Upload even if the stored certificate dates already match         |
| `--quiet` / `--debug`        | Output control                                                    |

## Exit codes

Exit code 0 means the BMC is serving the uploaded certificate (or genuinely had nothing to do); exit 2 means it is not — the caller should record a failed deployment and retry.

## Timeouts

Tuned to real hardware, not defaults:

- `SmcSSLCert.Upload` can take well over 30 seconds on H12/X12 boards — the BMC validates and stores the key material before responding, and may bounce its web backend as part of it. The tool allows 180 seconds.
- After the reboot, the poll for the served certificate allows 240 seconds (probing every 10) for the BMC web server to come back.

## Decision flow

The tool only uploads when it has to, but always verifies what the socket serves:

1. If the stored certificate's expiry matches the new one **and** the socket serves the matching leaf → nothing to do, exit 0.
2. If the stored certificate is current but the socket serves something older → skip the upload, reboot the BMC to apply what is already stored.
3. Otherwise → upload, confirm the BMC reports the new certificate, reboot, and poll until the served leaf's SHA-256 fingerprint matches the uploaded certificate.
