from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import OrganizationPlan, RunRecord, SongRecord


def default_data_dir() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    return Path(root) / "QQMusicOrganizer" if root else Path.home() / ".qqmusic-organizer"


class Storage:
    def __init__(self, root: Path | None = None):
        self.root = root or default_data_dir()
        self.exports = self.root / "exports"
        self.plans = self.root / "plans"
        self.runs = self.root / "runs"
        self.operations = self.root / "operations"
        self.smart_playlists = self.root / "smart_playlists"
        self.metadata = self.root / "metadata"
        for directory in (self.exports, self.plans, self.runs, self.operations, self.smart_playlists, self.metadata):
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def read_json(path: Path) -> Any:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def canonical_hash(data: Any) -> str:
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _safe_id(value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,100}", value):
            raise ValueError("invalid local record id")
        return value

    def save_export(
        self,
        songs: list[SongRecord],
        snapshot_type: str = "full",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, Path]:
        export_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        directory = self.exports / export_id
        directory.mkdir()
        payload = {
            **(metadata or {}),
            "id": export_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "count": len(songs),
            "snapshot_type": snapshot_type,
            "songs": [song.model_dump(mode="json") for song in songs],
        }
        self._write_json(directory / "liked.json", payload)
        with (directory / "liked.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["mid", "song_id", "song_type", "name", "singers", "album", "album_mid", "duration_seconds"])
            for song in songs:
                writer.writerow([song.mid, song.song_id or "", song.song_type, song.name, " / ".join(song.singers), song.album, song.album_mid, song.duration_seconds])
        artists: dict[str, int] = {}
        albums: dict[str, int] = {}
        for song in songs:
            for singer in song.singers:
                artists[singer] = artists.get(singer, 0) + 1
            if song.album:
                albums[song.album] = albums.get(song.album, 0) + 1
        summary = {
            "id": export_id,
            "count": len(songs),
            "top_singers": sorted(artists.items(), key=lambda item: (-item[1], item[0]))[:30],
            "top_albums": sorted(albums.items(), key=lambda item: (-item[1], item[0]))[:30],
        }
        self._write_json(directory / "summary.json", summary)
        return export_id, directory

    def load_export(self, export_id: str) -> dict[str, Any]:
        return self.read_json(self.exports / self._safe_id(export_id) / "liked.json")

    def load_export_summary(self, export_id: str) -> dict[str, Any]:
        return self.read_json(self.exports / self._safe_id(export_id) / "summary.json")

    def latest_full_export(self) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for path in self.exports.glob("*/liked.json"):
            try:
                payload = self.read_json(path)
            except (OSError, ValueError):
                continue
            if payload.get("snapshot_type", "full") == "full":
                candidates.append(payload)
        return max(candidates, key=lambda item: str(item.get("created_at", "")), default=None)

    def save_smart_playlist(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload)
        data.setdefault("id", "smart-" + secrets.token_hex(6))
        self._safe_id(str(data["id"]))
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._write_json(self.smart_playlists / f"{data['id']}.json", data)
        return data

    def load_smart_playlist(self, smart_playlist_id: str) -> dict[str, Any]:
        return self.read_json(self.smart_playlists / f"{self._safe_id(smart_playlist_id)}.json")

    def find_smart_playlist(self, name: str) -> dict[str, Any] | None:
        matches = []
        for path in self.smart_playlists.glob("*.json"):
            payload = self.read_json(path)
            if str(payload.get("name", "")).casefold() == name.casefold():
                matches.append(payload)
        if len(matches) > 1:
            raise RuntimeError(f"multiple local smart-playlist rules have the exact name: {name}")
        return matches[0] if matches else None

    def load_metadata(self, mid: str, max_age_days: int = 30) -> dict[str, Any] | None:
        path = self.metadata / f"{hashlib.sha256(mid.encode('utf-8')).hexdigest()}.json"
        try:
            payload = self.read_json(path)
            cached_at = datetime.fromisoformat(payload["cached_at"])
        except (FileNotFoundError, KeyError, TypeError, ValueError):
            return None
        if cached_at < datetime.now(timezone.utc) - timedelta(days=max_age_days):
            return None
        return payload

    def save_metadata(self, mid: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = {**payload, "mid": mid, "cached_at": datetime.now(timezone.utc).isoformat()}
        key = hashlib.sha256(mid.encode("utf-8")).hexdigest()
        self._write_json(self.metadata / f"{key}.json", data)
        return data

    def save_plan(self, plan: OrganizationPlan) -> None:
        self._write_json(self.plans / f"{plan.id}.json", plan.model_dump(mode="json"))

    def load_plan(self, plan_id: str) -> OrganizationPlan:
        plan = OrganizationPlan.model_validate(self.read_json(self.plans / f"{self._safe_id(plan_id)}.json"))
        if plan.status == "finalized":
            expected = self.canonical_hash(plan.model_dump(exclude={"sha256"}, mode="json"))
            if not plan.sha256 or not secrets.compare_digest(plan.sha256, expected):
                raise ValueError("finalized plan hash verification failed")
        return plan

    def set_write_capability(self, enabled: bool, detail: str) -> None:
        self._write_json(
            self.root / "capabilities.json",
            {"write_enabled": enabled, "detail": detail, "checked_at": datetime.now(timezone.utc).isoformat()},
        )

    def write_capability(self) -> dict[str, Any]:
        try:
            return self.read_json(self.root / "capabilities.json")
        except FileNotFoundError:
            return {"write_enabled": False, "detail": "write capability has not been probed"}

    def start_session(self) -> dict[str, str]:
        session = {
            "id": secrets.token_urlsafe(24),
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._write_json(self.root / "session.json", session)
        return session

    def active_session(self) -> dict[str, str]:
        try:
            session = self.read_json(self.root / "session.json")
        except FileNotFoundError as error:
            raise RuntimeError("no active QQ Music organizer session") from error
        if not isinstance(session.get("id"), str) or len(session["id"]) < 20:
            raise RuntimeError("active session file is invalid")
        return session

    def save_run(self, run: RunRecord) -> None:
        self._write_json(self.runs / f"{run.id}.json", run.model_dump(mode="json"))

    def load_run(self, run_id: str) -> RunRecord:
        return RunRecord.model_validate(self.read_json(self.runs / f"{self._safe_id(run_id)}.json"))

    def save_operation(self, action: str, payload: dict[str, Any]) -> Path:
        operation_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
        path = self.operations / f"{operation_id}.json"
        self._write_json(
            path,
            {"id": operation_id, "action": action, "created_at": datetime.now(timezone.utc).isoformat(), **payload},
        )
        return path
