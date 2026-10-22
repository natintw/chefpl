"""FileStore：匯入原檔與收據照片（Railway Storage Bucket，S3 相容；開發文件 12 節）。

- LocalFileStore：本機資料夾（開發、測試）
- S3FileStore：Railway Storage Bucket；下載一律用短效簽名網址（bucket 為私有）
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol

from app.config import get_settings


class FileStore(Protocol):
    def put(self, prefix: str, file_name: str, data: bytes, content_type: str) -> str: ...
    def get(self, key: str) -> bytes: ...
    def signed_url(self, key: str) -> str: ...


class LocalFileStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, prefix: str, file_name: str, data: bytes, content_type: str) -> str:
        key = f"{prefix}{uuid.uuid4().hex}/{Path(file_name).name}"
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def signed_url(self, key: str) -> str:
        return f"file://{(self.root / key).resolve()}"


class S3FileStore:
    def __init__(self) -> None:
        import boto3  # 只有正式環境需要

        s = get_settings()
        self.bucket = s.bucket
        self.ttl = s.signed_url_ttl_sec
        self.client = boto3.client("s3", endpoint_url=s.endpoint, region_name=s.region,
                                   aws_access_key_id=s.access_key_id, aws_secret_access_key=s.secret_access_key)

    def put(self, prefix: str, file_name: str, data: bytes, content_type: str) -> str:
        key = f"{prefix}{uuid.uuid4().hex}/{Path(file_name).name}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def signed_url(self, key: str) -> str:
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key},
                                                  ExpiresIn=self.ttl)


_store = None


def get_file_store():
    global _store
    if _store is None:
        s = get_settings()
        _store = S3FileStore() if (s.file_store_mode == "s3" and not s.use_mocks) else LocalFileStore(s.local_file_dir)
    return _store
