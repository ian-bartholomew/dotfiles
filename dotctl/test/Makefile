REPO := $(shell cd ../.. && pwd)
e2e: e2e-ubuntu e2e-debian e2e-arch
e2e-ubuntu:
	podman build -t dotctl-e2e-ubuntu --build-arg BASE=ubuntu:24.04 -f Dockerfile $(REPO) && podman run --rm dotctl-e2e-ubuntu
e2e-debian:
	podman build -t dotctl-e2e-debian --build-arg BASE=debian:12 -f Dockerfile $(REPO) && podman run --rm dotctl-e2e-debian
e2e-arch:
	podman build -t dotctl-e2e-arch --build-arg BASE=archlinux:latest -f Dockerfile $(REPO) && podman run --rm dotctl-e2e-arch
