# The debug volume: debugpy and sitecustomize.py at /opt/debugpy, nothing else. The `debugpy`
# service in ../docker-compose.yaml runs this image once; Docker copies /opt/debugpy into the
# empty named volume, and the chassis and the workload mount it read-only. Neither image changes.
# Base pinned by digest, the same as the platform's images. debugpy pinned by hash
# (requirements.txt). Bump both on purpose, in their own commit.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

FROM ${PYTHON_IMAGE}
COPY requirements.txt /tmp/debugpy-requirements.txt
# `--target`: debugpy lands in its own folder, not in any interpreter's site-packages. Owned by
# root, readable by every uid (the chassis runs as 10001, the workload as 10002).
RUN pip install --no-cache-dir --disable-pip-version-check --require-hashes --only-binary=:all: \
        --target /opt/debugpy -r /tmp/debugpy-requirements.txt \
    && rm /tmp/debugpy-requirements.txt
COPY sitecustomize.py /opt/debugpy/sitecustomize.py
# suggested: 65534 (nobody). The container only exits; it runs no code of ours.
USER 65534:65534
CMD ["true"]
