"""Testcontainers helpers for the real-adapter bindings (PoC-4): `valkey.py`, `minio.py`,
`kafka.py`, `dapr.py`, one per service, each added by the package that binds that adapter.

Used only by `network` tests (`make test-integration`); never imported by the chassis. Import
`testcontainers.community.*`, not the deprecated `testcontainers.<name>` aliases.
"""
