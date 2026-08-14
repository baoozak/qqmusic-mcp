from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SongRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mid: str = Field(min_length=1, max_length=128)
    song_id: int | None = None
    song_type: int = Field(default=0, ge=0)
    name: str = Field(min_length=1, max_length=500)
    singers: list[str] = Field(default_factory=list, max_length=30)
    album: str = Field(default="", max_length=500)
    album_mid: str = Field(default="", max_length=128)
    duration_seconds: int = Field(default=0, ge=0, le=86400)

    @field_validator("singers")
    @classmethod
    def validate_singers(cls, value: list[str]) -> list[str]:
        return [item.strip()[:200] for item in value if item.strip()]


class SmartPlaylistRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(default="", max_length=100)
    singer: str = Field(default="", max_length=100)
    album: str = Field(default="", max_length=200)
    min_duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    max_duration_seconds: int | None = Field(default=None, ge=0, le=86400)
    limit: int = Field(default=200, ge=1, le=1000)
    deduplicate: bool = True

    @model_validator(mode="after")
    def duration_range_is_valid(self) -> SmartPlaylistRule:
        if self.min_duration_seconds is not None and self.max_duration_seconds is not None:
            if self.min_duration_seconds > self.max_duration_seconds:
                raise ValueError("min_duration_seconds cannot exceed max_duration_seconds")
        return self


class SmartPlaylistBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    song_mids: list[str] = Field(min_length=1, max_length=1000)

    @field_validator("song_mids")
    @classmethod
    def unique_song_mids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("song_mids must be unique")
        return value


class TaxonomyItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    song_mid: str = Field(min_length=1, max_length=128)
    targets: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("targets")
    @classmethod
    def unique_targets(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("targets must be unique")
        return value

    @model_validator(mode="after")
    def low_confidence_goes_to_review(self) -> Assignment:
        if self.confidence < 0.65 and self.targets != ["needs_review"]:
            raise ValueError("low-confidence songs must only target needs_review")
        return self


class OrganizationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    export_id: str
    revision: int = Field(default=1, ge=1)
    status: Literal["draft", "finalized"] = "draft"
    created_at: str = Field(default_factory=utc_now)
    finalized_at: str | None = None
    taxonomy: list[TaxonomyItem] = Field(default_factory=list, max_length=30)
    assignments: dict[str, Assignment] = Field(default_factory=dict)
    sha256: str | None = None

    @model_validator(mode="after")
    def taxonomy_is_valid(self) -> OrganizationPlan:
        keys = [item.key for item in self.taxonomy]
        names = [item.name.casefold() for item in self.taxonomy]
        if len(keys) != len(set(keys)) or len(names) != len(set(names)):
            raise ValueError("taxonomy keys and names must be unique")
        allowed = set(keys)
        for assignment in self.assignments.values():
            if not set(assignment.targets) <= allowed:
                raise ValueError("assignment references an unknown taxonomy key")
            if assignment.confidence < 0.65 and assignment.targets != ["needs_review"]:
                raise ValueError("low-confidence songs must only target needs_review")
        return self


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    plan_id: str
    plan_sha256: str
    status: Literal["running", "completed", "failed", "rolled_back"] = "running"
    created_at: str = Field(default_factory=utc_now)
    completed_at: str | None = None
    created_playlists: dict[str, int] = Field(default_factory=dict)
    added_songs: dict[str, list[str]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
