# supermicro-ipmi-cert

Single-file Python tool (`supermicro_ipmi_cert.py`) that deploys TLS certs to
Supermicro BMCs via Redfish and verifies the served certificate. Promoted out
of the `containers` monorepo 2026-08-02 (history before then lives there).

## Invariants — do not "fix" these

- **Leaf-only upload is a FIRMWARE constraint.** `SmcSSLCert.Upload` 400s on
  any multi-cert PEM, and `ReplaceCertificate` only works with its own
  `GenerateCSR` flow (verified live 2026-08-02, H13 fw 01.05.09). The BMCs
  always serve a chainless leaf; monitoring probes them without verification.
- **The socket is authoritative.** Never report success from API responses
  alone; the served-fingerprint checks exist because a BMC served an expired
  cert for 3.5 months while deploy logs said success.
- **`Manager.Reset` needs `{"ResetType": "GracefulRestart"}`.** An empty body
  returns 200 without restarting on H12/X12.
- **kubectl in the image is load-bearing** — the talos-cluster deploy Job
  wrapper (`kubernetes/apps/infrastructure/certwarden/cert-deployment/supermicro/`)
  shells into this image and kubectl-reads its credentials secret.
- **`UPLOAD_TIMEOUT = 180`**: H12/X12 uploads genuinely take >30s.

## Commands

- Tests: `pip install -r requirements.txt -r requirements-dev.txt && pytest`
- Image: `ghcr.io/lukeevanstech/supermicro-ipmi-cert` — built by
  `.github/workflows/build-and-push.yaml`, tagged `latest` + `<VERSION>` +
  `main-<sha>` on main.

## Release flow

Bump `VERSION`, merge; talos-cluster pins `<version>@sha256:<digest>` in the
scripts ConfigMap (two refs — keep them identical). Renovate proposes the
bump there; consumers are only talos-cluster deploy Jobs.

Lint is super-linter via shared-workflows (`soft-launch: false`); configs in
`.github/linters/`. Run the real super-linter image locally before pushing.
