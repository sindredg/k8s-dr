# Tools for the backup and restore Jobs: pg_dump and pg_restore from the same
# PostgreSQL release as the server, age, curl, jq, and kubectl. The scripts
# ship in a ConfigMap from deploy/apps/gitea, so they change without a
# rebuild. See decision 0007.
FROM postgres:18.6-trixie@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722

ARG KUBECTL_VERSION=1.36.2
ARG KUBECTL_SHA256=1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82

RUN apt-get update \
    && apt-get install --yes --no-install-recommends age ca-certificates curl jq \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLo /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/v${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum --check --strict \
    && chmod 0755 /usr/local/bin/kubectl

USER 1000:1000
ENTRYPOINT []
