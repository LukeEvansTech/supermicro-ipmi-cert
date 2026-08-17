FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31 AS builder

ARG KUBECTL_VERSION=v1.32.0
ARG TARGETARCH=amd64

WORKDIR /downloads

# kubectl is load-bearing: the Certwarden deploy Job (talos-cluster
# cert-deployment/supermicro) runs this image with a shell command that
# kubectl-reads its credentials secret before invoking the tool.
RUN wget -q -O kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" && \
    chmod +x kubectl

# Final runtime image
FROM python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

LABEL org.opencontainers.image.source="https://github.com/LukeEvansTech/supermicro-ipmi-cert"
LABEL org.opencontainers.image.description="Supermicro IPMI Certificate Deployment Tool for Cert Warden (Redfish/X12/X13/H13 only)"
LABEL org.opencontainers.image.licenses="GPL-2.0"

# Install runtime dependencies. ca-certificates is unpinned on purpose: its
# version tracks the Alpine release and a pin would break on every base-image
# bump for zero reproducibility gain.
# hadolint ignore=DL3018
RUN apk add --no-cache \
    ca-certificates \
    && rm -rf /var/cache/apk/*

# Copy kubectl from builder
COPY --from=builder /downloads/kubectl /usr/local/bin/kubectl

# Install Python packages
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Create app directory and set permissions for nobody user (65534:65534)
# Alpine already has nobody:nobody (65534:65534) user
RUN mkdir -p /app && \
    chown -R nobody:nobody /app

# Copy application
COPY supermicro_ipmi_cert.py /app/supermicro_ipmi_cert.py
RUN chmod +x /app/supermicro_ipmi_cert.py && \
    chown nobody:nobody /app/supermicro_ipmi_cert.py

# Switch to non-root nobody user
USER nobody

WORKDIR /app

# Verify installations
RUN kubectl version --client && \
    python3 -c "import requests; import OpenSSL; print('Python packages OK')"

ENTRYPOINT ["python3", "/app/supermicro_ipmi_cert.py"]
