# supermicro-ipmi-cert

Deploys TLS certificates to Supermicro BMCs (X12 / X13 / H13 generations) over the Redfish API, and — critically — verifies that the BMC is _actually serving_ the uploaded certificate before reporting success.

It runs as a container spawned by [Cert Warden](https://www.certwarden.com/) post-processing: Cert Warden renews a Let's Encrypt certificate, a deploy Job runs this image, and the BMC comes back serving the new cert with no human involved.

## Why verification is the whole point

Uploading a certificate to a Supermicro BMC is not the same as the BMC serving it. Two silent failure modes were observed in production (both fixed in 0.2.0):

1. **The reboot that never happened.** `Manager.Reset` with an empty POST body returns HTTP 200 on H12/X12 firmware without actually restarting — the old certificate stays live. The tool always sends `{"ResetType": "GracefulRestart"}` and treats a failed reboot as a deployment failure (exit 2).
2. **Stored ≠ served.** The BMC can report the new certificate as installed while the socket still serves the old one. After reboot, the tool polls `:443` until the served leaf matches the uploaded certificate's SHA-256 fingerprint, and fails if it never does. The "nothing to do" fast path is also socket-verified: if the stored cert is current but the served one is stale, the tool reboots the BMC to self-heal.

One BMC served an expired certificate for 3.5 months while every deploy log said success — hence: **trust the socket, not the API.**

## Where to next

- [Usage](usage.md) — the CLI, exit codes, and timeout tuning
- [Firmware constraint](firmware-constraint.md) — why these BMCs serve a chainless leaf, with the test evidence
- [Deployment](deployment.md) — how the Cert Warden → Kubernetes Job pipeline invokes this image

## History

This tool lived in the [containers](https://github.com/LukeEvansTech/containers) monorepo (`apps/supermicro-ipmi-cert`) until 2026-08-02; history before then is in that repository. Based on [Jari Turkia's ipmi-updater.py](https://github.com/jturkia/supermicro-ipmi-updater), reduced to Redfish-only boards and extended with served-certificate verification. Licensed GPL-2.0, inherited from the original.
