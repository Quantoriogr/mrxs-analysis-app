from io import BytesIO
from minio import Minio
from minio.error import S3Error

from .config import config


class MinioObjectContext:
    def __init__(self, response):
        self.ctx = response

    def __enter__(self):
        return self.ctx

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.ctx.close()
        self.ctx.release_conn()
        return False


class ObjectStorage:
    def __init__(self) -> None:
        self.client = Minio(
            config.minio_endpoint,
            access_key=config.minio_access_key,
            secret_key=config.minio_secret_key,
            secure=config.minio_secure,
        )
        self.bucket = config.minio_bucket

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def save_object(self, object_name: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            self.bucket,
            object_name,
            BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    def get_object(self, object_name: str) -> bytes:
        response = self.client.get_object(self.bucket, object_name)
        with MinioObjectContext(response) as ctx:
            return ctx.read()

    def exists(self, object_name: str) -> bool:
        try:
            self.client.stat_object(self.bucket, object_name)
            return True
        except S3Error:
            return False


storage = ObjectStorage()
