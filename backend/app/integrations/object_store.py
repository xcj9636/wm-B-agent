"""Object-store boundary used by quarantined media asset ingestion."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class StoredObjectMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=1000)
    size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MediaObjectStore(Protocol):
    backend_name: str

    def head(self, key: str) -> StoredObjectMetadata: ...

