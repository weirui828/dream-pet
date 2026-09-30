"""Where videos, stills and page snapshots go: local disk by default, or S3-compatible storage."""

from __future__ import annotations

from pathlib import Path

from dreampet.config import AppConfig


class MediaStore:
    def __init__(self, cfg: AppConfig, root: Path | None = None):
        self.cfg = cfg.media
        self.root = root or (cfg.data_path / cfg.media.path)
        self.root.mkdir(parents=True, exist_ok=True)
        self._s3 = None

    def local_path(self, rel: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def publish(self, rel: str) -> str:
        """Make a file written at local_path(rel) durable; returns its URI."""
        if self.cfg.backend == "s3":
            s3 = self._client()
            s3.upload_file(str(self.root / rel), self.cfg.s3_bucket, rel)
            return f"s3://{self.cfg.s3_bucket}/{rel}"
        return f"media://{rel}"

    def resolve(self, uri: str) -> Path | None:
        if uri.startswith("media://"):
            p = (self.root / uri[len("media://"):]).resolve()
            return p if str(p).startswith(str(self.root.resolve())) and p.exists() else None
        return None

    def presigned(self, uri: str, expires: int = 3600) -> str | None:
        if uri.startswith("s3://"):
            bucket, key = uri[5:].split("/", 1)
            return self._client().generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                                          ExpiresIn=expires)
        return None

    def _client(self):
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", endpoint_url=self.cfg.s3_endpoint, aws_access_key_id=self.cfg.s3_access_key,
                                    aws_secret_access_key=self.cfg.s3_secret_key)
        return self._s3


def http_url(uri: str | None) -> str | None:
    """The API path a client uses to load a media URI."""
    if not uri:
        return None
    if uri.startswith("media://"):
        return "/api/v1/media/" + uri[len("media://"):]
    if uri.startswith("s3://"):
        return "/api/v1/media-s3/" + uri[5:]
    return uri
