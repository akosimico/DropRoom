from app.storage import base, local, s3

__all__ = ["base", "local", "s3"]

get_storage_backend = s3.get_storage_backend