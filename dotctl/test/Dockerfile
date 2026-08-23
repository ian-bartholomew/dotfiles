ARG BASE=ubuntu:24.04
FROM ${BASE}
ARG GO_VERSION=1.22.12
RUN if command -v apt-get >/dev/null; then apt-get update && apt-get install -y git curl ca-certificates; \
    elif command -v pacman >/dev/null; then pacman -Syu --noconfirm git curl; fi
RUN set -eux; case "$(uname -m)" in x86_64) g=amd64;; aarch64|arm64) g=arm64;; esac; \
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${g}.tar.gz" | tar -C /usr/local -xz
ENV PATH="/usr/local/go/bin:${PATH}"
COPY . /repo
RUN chmod +x /repo/dotctl/test/e2e.sh
CMD ["/repo/dotctl/test/e2e.sh"]
