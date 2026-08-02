# Deployment

The image is published as `ghcr.io/lukeevanstech/supermicro-ipmi-cert`, built by this repository's CI from the `VERSION` file (tags: `latest`, `<VERSION>`, `main-<sha>`).

## Cert Warden pipeline

The consumer is the set of Cert Warden deploy Jobs in [talos-cluster](https://github.com/LukeEvansTech/talos-cluster) (`kubernetes/apps/infrastructure/certwarden/cert-deployment/supermicro/`):

1. Cert Warden renews a certificate and runs its post-process script.
2. The wrapper creates a short-lived Kubernetes secret holding the BMC credentials and certificate material, owner-bound to the Job so it is garbage-collected with it.
3. A Job runs this image. Its shell step reads the secret using the `kubectl` bundled in the image — that binary is load-bearing, not a convenience — then invokes the tool with the flags described in [Usage](usage.md).
4. The Job's exit code is the deployment verdict: the tool exits non-zero unless the BMC is verifiably serving the expected certificate.

## Version pinning

talos-cluster pins the image as `<version>@sha256:<digest>` in the deploy ConfigMap (two references that must stay identical). Renovate proposes digest bumps when a new version is published.

## Running it manually

```console
docker run --rm \
  -v "$PWD:/certs:ro" \
  ghcr.io/lukeevanstech/supermicro-ipmi-cert:latest \
  --ipmi-url https://bmc.example.com \
  --model H13 \
  --key-file /certs/privkey.pem \
  --cert-file /certs/cert.pem \
  --username ADMIN \
  --password '...'
```

The container runs as `nobody` (65534) and persists nothing.
