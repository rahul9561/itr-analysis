"""Upload handling that keeps documents in memory and enforces a hard size cap.

Source documents are never written to a temporary file on disk.
"""

from __future__ import annotations

from io import BytesIO

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.files.uploadhandler import FileUploadHandler


class UploadTooLarge(Exception):
    pass


class BoundedMemoryUploadHandler(FileUploadHandler):
    def __init__(self, request=None, max_bytes: int = 10 * 1024 * 1024):
        super().__init__(request)
        self.max_bytes = max_bytes

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.buffer = BytesIO()
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        self.received += len(raw_data)
        if self.received > self.max_bytes:
            raise UploadTooLarge()
        self.buffer.write(raw_data)
        return None

    def file_complete(self, file_size):
        self.buffer.seek(0)
        return InMemoryUploadedFile(
            file=self.buffer,
            field_name=self.field_name,
            name=self.file_name,
            content_type=self.content_type,
            size=file_size,
            charset=self.charset,
            content_type_extra=self.content_type_extra,
        )
