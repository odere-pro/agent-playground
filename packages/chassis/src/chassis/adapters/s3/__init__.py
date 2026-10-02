"""S3 adapters (PoC-4): `config.S3Config`, a `ConfigPort` over any S3 API (MinIO, AWS S3), on the
`minio` SDK. The SDK is imported only here and loaded lazily by `profiles.REGISTRY`.
"""
