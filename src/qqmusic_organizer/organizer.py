from __future__ import annotations

import secrets
import csv
import re
from collections import Counter
from typing import Any

from .models import Assignment, OrganizationPlan, RunRecord, SmartPlaylistBucket, SmartPlaylistRule, SongRecord, TaxonomyItem, utc_now
from .qqmusic import QQMusicClient
from .storage import Storage


NEEDS_REVIEW = TaxonomyItem(
    key="needs_review",
    name="待整理",
    description="AI 置信度不足，保留给人工确认的歌曲。",
)
PROBE_PLAYLIST_PREFIX = "AI整理-接口测试-"


class Organizer:
    def __init__(self, client: QQMusicClient, storage: Storage):
        self.client = client
        self.storage = storage

    async def status(self) -> dict[str, Any]:
        playlists = await self.client.list_created_playlists()
        liked = next((playlist for playlist in playlists if playlist.directory_id == 201), None)
        capability = self.storage.write_capability()
        return {
            "authenticated": True,
            "account_hint": f"***{self.client.uin[-4:]}" if self.client.uin else None,
            "liked_found": liked is not None,
            "liked_song_count": liked.song_count if liked else None,
            "created_playlist_count": len(playlists),
            "write_enabled": bool(capability.get("write_enabled")),
            "write_capability_detail": capability.get("detail"),
        }

    async def list_playlists(self) -> list[dict[str, Any]]:
        return [playlist.__dict__ for playlist in await self.client.list_created_playlists()]

    async def get_playlist(self, directory_id: int) -> dict[str, Any]:
        playlist = await self._playlist(directory_id)
        if playlist.playlist_id is None:
            raise RuntimeError("playlist is not readable")
        songs = await self.client.get_playlist_songs(playlist.playlist_id)
        return {"playlist": playlist.__dict__, "songs": [song.model_dump(mode="json") for song in songs]}

    async def analyze_library(self) -> dict[str, Any]:
        playlists, songs_by_directory = await self._library_snapshot()
        liked = next((item for item in playlists if item.directory_id == 201), None)
        custom = [item for item in playlists if item.directory_id != 201]
        all_mids = {song.mid for songs in songs_by_directory.values() for song in songs}
        liked_mids = {song.mid for song in songs_by_directory.get(201, [])}
        custom_mids = {song.mid for item in custom for song in songs_by_directory.get(item.directory_id, [])}
        duplicate_groups = self._duplicate_groups(playlists, songs_by_directory)
        title_duplicate_groups = self._title_duplicate_groups(playlists, songs_by_directory)
        empty = [item.__dict__ for item in custom if not songs_by_directory.get(item.directory_id)]
        return {
            "playlist_count": len(playlists),
            "custom_playlist_count": len(custom),
            "song_occurrence_count": sum(len(songs) for songs in songs_by_directory.values()),
            "unique_song_count": len(all_mids),
            "liked_song_count": len(liked_mids),
            "unorganized_liked_count": len(liked_mids - custom_mids),
            "duplicate_group_count": len(duplicate_groups),
            "same_title_group_count": len(title_duplicate_groups),
            "empty_playlist_count": len(empty),
            "duplicate_groups": duplicate_groups[:100],
            "same_title_groups": title_duplicate_groups[:100],
            "empty_playlists": empty,
            "unorganized_liked_mids": sorted(liked_mids - custom_mids)[:200],
            "liked_playlist_found": liked is not None,
        }

    async def find_duplicates(self) -> dict[str, Any]:
        playlists, songs_by_directory = await self._library_snapshot()
        groups = self._duplicate_groups(playlists, songs_by_directory)
        title_groups = self._title_duplicate_groups(playlists, songs_by_directory)
        return {"count": len(groups), "groups": groups, "same_title_count": len(title_groups), "same_title_groups": title_groups}

    async def find_empty_playlists(self) -> dict[str, Any]:
        playlists, songs_by_directory = await self._library_snapshot()
        empty = [item.__dict__ for item in playlists if item.directory_id != 201 and not songs_by_directory.get(item.directory_id)]
        return {"count": len(empty), "playlists": empty}

    async def find_unorganized_songs(self) -> dict[str, Any]:
        playlists, songs_by_directory = await self._library_snapshot()
        liked_songs = songs_by_directory.get(201, [])
        custom_mids = {song.mid for item in playlists if item.directory_id != 201 for song in songs_by_directory.get(item.directory_id, [])}
        songs = [song for song in liked_songs if song.mid not in custom_mids]
        return {"count": len(songs), "songs": [song.model_dump(mode="json") for song in songs]}

    async def compare_playlists(self, left_directory_id: int, right_directory_id: int) -> dict[str, Any]:
        await self._playlist(left_directory_id)
        await self._playlist(right_directory_id)
        _, songs_by_directory = await self._library_snapshot()
        left = {song.mid: song for song in songs_by_directory.get(left_directory_id, [])}
        right = {song.mid: song for song in songs_by_directory.get(right_directory_id, [])}
        return {
            "left_directory_id": left_directory_id,
            "right_directory_id": right_directory_id,
            "intersection_count": len(left.keys() & right.keys()),
            "only_left": [left[mid].model_dump(mode="json") for mid in sorted(left.keys() - right.keys())],
            "only_right": [right[mid].model_dump(mode="json") for mid in sorted(right.keys() - left.keys())],
        }

    async def create_smart_playlist(self, name: str, source_directory_id: int, rule: SmartPlaylistRule) -> dict[str, Any]:
        await self._playlist(source_directory_id)
        _, songs_by_directory = await self._library_snapshot()
        songs = self._filter_songs(songs_by_directory.get(source_directory_id, []), rule)
        if not songs:
            raise ValueError("smart playlist rule matched no songs")
        return await self._create_playlist_from_songs(name, songs)

    async def merge_playlists(self, name: str, source_directory_ids: list[int]) -> dict[str, Any]:
        if len(source_directory_ids) < 2 or len(source_directory_ids) > 20:
            raise ValueError("merge requires 2-20 source playlists")
        for directory_id in source_directory_ids:
            await self._playlist(directory_id)
        _, songs_by_directory = await self._library_snapshot()
        unique: dict[str, SongRecord] = {}
        for directory_id in source_directory_ids:
            for song in songs_by_directory.get(directory_id, []):
                unique.setdefault(song.mid, song)
        if not unique:
            raise ValueError("source playlists contain no readable songs")
        return await self._create_playlist_from_songs(name, list(unique.values()))

    async def split_playlist(self, source_directory_id: int, buckets: list[SmartPlaylistBucket]) -> dict[str, Any]:
        if not buckets or len(buckets) > 20:
            raise ValueError("split requires 1-20 buckets")
        await self._playlist(source_directory_id)
        _, songs_by_directory = await self._library_snapshot()
        source = {song.mid: song for song in songs_by_directory.get(source_directory_id, [])}
        if not source:
            raise ValueError("source playlist contains no readable songs")
        names = [bucket.name.casefold() for bucket in buckets]
        if len(names) != len(set(names)):
            raise ValueError("bucket names must be unique")
        known: set[str] = set()
        for bucket in buckets:
            if any(mid not in source for mid in bucket.song_mids):
                raise ValueError(f"bucket {bucket.name} contains a song outside the source playlist")
            overlap = known.intersection(bucket.song_mids)
            if overlap:
                raise ValueError(f"song appears in multiple buckets: {next(iter(overlap))}")
            known.update(bucket.song_mids)
        results = []
        for bucket in buckets:
            results.append(await self._create_playlist_from_songs(bucket.name, [source[mid] for mid in bucket.song_mids]))
        return {"source_directory_id": source_directory_id, "created": results}

    async def create_playlist(self, name: str) -> dict[str, Any]:
        self._require_write_capability()
        directory_id = await self.client.create_playlist(name)
        playlist = await self._playlist(directory_id)
        self.storage.save_operation("create_playlist", {"directory_id": directory_id, "name": name})
        return playlist.__dict__

    async def add_songs(self, directory_id: int, songs: list[SongRecord]) -> dict[str, Any]:
        self._require_write_capability()
        playlist = await self._writable_playlist(directory_id)
        if not songs or len(songs) > 20:
            raise ValueError("add_songs accepts 1-20 songs per call")
        if len({song.mid for song in songs}) != len(songs):
            raise ValueError("songs must contain unique mids")
        await self.client.add_songs(directory_id, songs)
        refreshed = await self._playlist(directory_id)
        if refreshed.playlist_id is None:
            raise RuntimeError("playlist is not readable after adding songs")
        observed = {song.mid for song in await self.client.get_playlist_songs(refreshed.playlist_id)}
        missing = {song.mid for song in songs} - observed
        if missing:
            raise RuntimeError(f"QQ Music did not confirm {len(missing)} added songs")
        path = self.storage.save_operation(
            "add_songs", {"directory_id": directory_id, "song_mids": [song.mid for song in songs]}
        )
        return {"playlist": playlist.__dict__, "added": len(songs), "operation_log": str(path)}

    async def remove_songs(self, directory_id: int, songs: list[SongRecord]) -> dict[str, Any]:
        self._require_write_capability()
        playlist = await self._writable_playlist(directory_id)
        if not songs or len(songs) > 20:
            raise ValueError("remove_songs accepts 1-20 songs per call")
        if len({song.mid for song in songs}) != len(songs):
            raise ValueError("songs must contain unique mids")
        await self.client.remove_songs(directory_id, songs)
        refreshed = await self._playlist(directory_id)
        if refreshed.playlist_id is None:
            raise RuntimeError("playlist is not readable after removing songs")
        observed = {song.mid for song in await self.client.get_playlist_songs(refreshed.playlist_id)}
        remaining = {song.mid for song in songs} & observed
        if remaining:
            raise RuntimeError(f"QQ Music did not confirm {len(remaining)} removed songs")
        path = self.storage.save_operation(
            "remove_songs", {"directory_id": directory_id, "song_mids": [song.mid for song in songs]}
        )
        return {"playlist": playlist.__dict__, "removed": len(songs), "operation_log": str(path)}

    async def delete_playlist(self, directory_id: int) -> dict[str, Any]:
        self._require_write_capability()
        playlist = await self._writable_playlist(directory_id)
        if playlist.playlist_id is None:
            raise RuntimeError("playlist is not readable")
        songs = await self.client.get_playlist_songs(playlist.playlist_id)
        if songs:
            raise ValueError("delete_playlist refuses non-empty playlists; remove songs first")
        await self.client.delete_playlist(directory_id)
        if any(item.directory_id == directory_id for item in await self.client.list_created_playlists()):
            raise RuntimeError("QQ Music did not confirm playlist deletion")
        path = self.storage.save_operation(
            "delete_playlist", {"directory_id": directory_id, "name": playlist.name}
        )
        return {"deleted": True, "directory_id": directory_id, "operation_log": str(path)}

    async def export_liked(self) -> dict[str, Any]:
        liked, songs = await self.client.export_liked()
        export_id, path = self.storage.save_export(songs)
        return {
            "export_id": export_id,
            "count": len(songs),
            "playlist_name": liked.name or "我喜欢",
            "path": str(path),
        }

    def export_summary(self, export_id: str) -> dict[str, Any]:
        directory = self.storage.exports / export_id
        return self.storage.read_json(directory / "summary.json")

    def export_page(self, export_id: str, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        if page < 1 or page_size < 1 or page_size > 200:
            raise ValueError("page must be >= 1 and page_size must be 1-200")
        payload = self.storage.load_export(export_id)
        songs = payload["songs"]
        start = (page - 1) * page_size
        return {
            "export_id": export_id,
            "total": len(songs),
            "page": page,
            "page_size": page_size,
            "songs": songs[start : start + page_size],
        }

    def create_plan(self, export_id: str) -> OrganizationPlan:
        self.storage.load_export(export_id)
        plan = OrganizationPlan(
            id="plan-" + secrets.token_hex(6),
            export_id=export_id,
            taxonomy=[NEEDS_REVIEW],
        )
        self.storage.save_plan(plan)
        return plan

    def set_taxonomy(self, plan_id: str, items: list[TaxonomyItem]) -> OrganizationPlan:
        plan = self.storage.load_plan(plan_id)
        self._require_draft(plan)
        taxonomy = [item for item in items if item.key != NEEDS_REVIEW.key] + [NEEDS_REVIEW]
        if not 2 <= len(taxonomy) <= 30:
            raise ValueError("taxonomy must contain 1-29 categories plus needs_review")
        plan.taxonomy = taxonomy
        plan.assignments = {}
        plan = OrganizationPlan.model_validate(plan.model_dump())
        self.storage.save_plan(plan)
        return plan

    def upsert_assignments(self, plan_id: str, assignments: list[Assignment]) -> OrganizationPlan:
        if not assignments or len(assignments) > 200:
            raise ValueError("submit 1-200 assignments per call")
        plan = self.storage.load_plan(plan_id)
        self._require_draft(plan)
        export = self.storage.load_export(plan.export_id)
        known_mids = {song["mid"] for song in export["songs"]}
        for assignment in assignments:
            if assignment.song_mid not in known_mids:
                raise ValueError(f"song {assignment.song_mid} is not in the source export")
            plan.assignments[assignment.song_mid] = assignment
        plan = OrganizationPlan.model_validate(plan.model_dump())
        self.storage.save_plan(plan)
        return plan

    def preview(self, plan_id: str) -> dict[str, Any]:
        plan = self.storage.load_plan(plan_id)
        export = self.storage.load_export(plan.export_id)
        counts = Counter(target for assignment in plan.assignments.values() for target in assignment.targets)
        total = len(export["songs"])
        assigned = len(plan.assignments)
        names = {item.key: item.name for item in plan.taxonomy}
        result = {
            "plan_id": plan.id,
            "status": plan.status,
            "sha256": plan.sha256,
            "total_songs": total,
            "assigned_songs": assigned,
            "unassigned_songs": total - assigned,
            "coverage": assigned / total if total else 1,
            "playlist_counts": {names[key]: value for key, value in counts.items()},
            "needs_review": counts.get("needs_review", 0),
        }
        directory = self.storage.plans / plan.id
        directory.mkdir(exist_ok=True)
        self.storage._write_json(directory / "preview.json", result)
        with (directory / "preview.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["song_mid", "name", "singers", "album", "targets", "confidence", "reason"])
            song_by_mid = {song["mid"]: song for song in export["songs"]}
            for mid, assignment in plan.assignments.items():
                song = song_by_mid[mid]
                writer.writerow(
                    [mid, song["name"], " / ".join(song["singers"]), song["album"], " / ".join(names[key] for key in assignment.targets), assignment.confidence, assignment.reason]
                )
        markdown = [
            f"# 整理计划 {plan.id}",
            "",
            f"- 总歌曲：{total}",
            f"- 已归类：{assigned}",
            f"- 未归类：{total - assigned}",
            f"- 待整理：{counts.get('needs_review', 0)}",
            "",
            "## 歌单",
            "",
        ]
        markdown.extend(f"- {names[key]}：{value}" for key, value in counts.items())
        (directory / "preview.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
        result["preview_path"] = str(directory)
        return result

    def finalize(self, plan_id: str) -> OrganizationPlan:
        plan = self.storage.load_plan(plan_id)
        self._require_draft(plan)
        export = self.storage.load_export(plan.export_id)
        mids = {song["mid"] for song in export["songs"]}
        missing = mids - set(plan.assignments)
        if missing:
            raise ValueError(f"plan is incomplete: {len(missing)} songs have no assignment")
        plan.status = "finalized"
        from .models import utc_now

        plan.finalized_at = utc_now()
        hash_payload = plan.model_dump(exclude={"sha256"})
        plan.sha256 = self.storage.canonical_hash(hash_payload)
        self.storage.save_plan(plan)
        return plan

    def revise(self, plan_id: str) -> OrganizationPlan:
        source = self.storage.load_plan(plan_id)
        if source.status != "finalized":
            raise ValueError("only a finalized plan can be revised")
        revised = source.model_copy(deep=True)
        revised.id = "plan-" + secrets.token_hex(6)
        revised.revision += 1
        revised.status = "draft"
        revised.created_at = utc_now()
        revised.finalized_at = None
        revised.sha256 = None
        self.storage.save_plan(revised)
        return revised

    async def probe_write(self) -> dict[str, Any]:
        await self._cleanup_stale_probe_playlists()
        liked, songs = await self.client.export_liked()
        candidate = next((song for song in songs if song.song_id), None)
        if candidate is None:
            raise ValueError("write probe needs one liked song with both MID and numeric song id")
        name = PROBE_PLAYLIST_PREFIX + secrets.token_hex(3)
        directory_id: int | None = None
        cleanup_errors: list[str] = []
        try:
            directory_id = await self.client.create_playlist(name)
            await self.client.add_songs(directory_id, [candidate])
            created = next((item for item in await self.client.list_created_playlists() if item.directory_id == directory_id), None)
            if created is None or created.playlist_id is None:
                raise RuntimeError("test playlist could not be read back")
            added = await self.client.get_playlist_songs(created.playlist_id)
            if candidate.mid not in {song.mid for song in added}:
                raise RuntimeError("test song was not present after add")
            await self.client.remove_songs(directory_id, [candidate])
        except Exception as error:
            self.storage.set_write_capability(False, f"probe failed: {type(error).__name__}")
            raise
        finally:
            if directory_id is not None:
                try:
                    await self.client.delete_playlist(directory_id)
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
        if cleanup_errors:
            self.storage.set_write_capability(False, "probe cleanup failed")
            raise RuntimeError(f"write probe cleanup failed for playlist {name}: {', '.join(cleanup_errors)}")
        self.storage.set_write_capability(True, "create/add/read/remove/delete probe succeeded")
        return {"write_enabled": True, "tested_with": liked.name or "我喜欢", "cleanup_succeeded": True}

    async def _cleanup_stale_probe_playlists(self) -> None:
        for playlist in await self.client.list_created_playlists():
            if playlist.directory_id == 201 or not playlist.name.startswith(PROBE_PLAYLIST_PREFIX):
                continue
            if playlist.playlist_id is None:
                raise RuntimeError("stale probe playlist is not readable; cleanup refused")
            if await self.client.get_playlist_songs(playlist.playlist_id):
                raise RuntimeError("stale probe playlist is not empty; cleanup refused")
            await self.client.delete_playlist(playlist.directory_id)

    async def _library_snapshot(self):
        playlists = await self.client.list_created_playlists()
        songs_by_directory: dict[int, list[SongRecord]] = {}
        for playlist in playlists:
            if playlist.playlist_id is not None:
                songs_by_directory[playlist.directory_id] = await self.client.get_playlist_songs(playlist.playlist_id)
        return playlists, songs_by_directory

    @staticmethod
    def _duplicate_groups(playlists, songs_by_directory) -> list[dict[str, Any]]:
        occurrences: dict[str, list[str]] = {}
        for playlist in playlists:
            for song in songs_by_directory.get(playlist.directory_id, []):
                occurrences.setdefault(song.mid, []).append(playlist.name)
        return [
            {"mid": mid, "playlist_count": len(names), "playlists": names}
            for mid, names in occurrences.items()
            if len(names) > 1
        ]

    @staticmethod
    def _title_duplicate_groups(playlists, songs_by_directory) -> list[dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for playlist in playlists:
            for song in songs_by_directory.get(playlist.directory_id, []):
                identity = re.sub(r"\s+", " ", f"{song.name}|{'/'.join(song.singers)}".casefold()).strip()
                group = groups.setdefault(identity, {"identity": identity, "mids": set(), "playlists": set()})
                group["mids"].add(song.mid)
                group["playlists"].add(playlist.name)
        return [
            {"identity": item["identity"], "mids": sorted(item["mids"]), "playlists": sorted(item["playlists"])}
            for item in groups.values()
            if len(item["mids"]) > 1
        ]

    @staticmethod
    def _filter_songs(songs: list[SongRecord], rule: SmartPlaylistRule) -> list[SongRecord]:
        result = []
        seen: set[str] = set()
        for song in songs:
            searchable = " ".join([song.name, song.album, *song.singers]).casefold()
            if rule.keyword and rule.keyword.casefold() not in searchable:
                continue
            if rule.singer and not any(rule.singer.casefold() in singer.casefold() for singer in song.singers):
                continue
            if rule.album and rule.album.casefold() not in song.album.casefold():
                continue
            if rule.min_duration_seconds is not None and song.duration_seconds < rule.min_duration_seconds:
                continue
            if rule.max_duration_seconds is not None and song.duration_seconds > rule.max_duration_seconds:
                continue
            if rule.deduplicate and song.mid in seen:
                continue
            seen.add(song.mid)
            result.append(song)
            if len(result) >= rule.limit:
                break
        return result

    async def _create_playlist_from_songs(self, name: str, songs: list[SongRecord]) -> dict[str, Any]:
        created = await self.create_playlist(name)
        directory_id = created["directory_id"]
        for start in range(0, len(songs), 20):
            await self.add_songs(directory_id, songs[start : start + 20])
        return {"playlist": await self.get_playlist(directory_id), "source_song_count": len(songs)}

    async def _playlist(self, directory_id: int):
        playlist = next(
            (item for item in await self.client.list_created_playlists() if item.directory_id == directory_id), None
        )
        if playlist is None:
            raise ValueError(f"created playlist not found: {directory_id}")
        return playlist

    async def _writable_playlist(self, directory_id: int):
        if directory_id == 201:
            raise PermissionError("the liked playlist can never be a write target")
        return await self._playlist(directory_id)

    def _require_write_capability(self) -> None:
        if not self.storage.write_capability().get("write_enabled"):
            raise PermissionError("write capability is disabled; run the write probe first")

    async def apply_plan(self, plan_id: str) -> dict[str, Any]:
        plan = self.storage.load_plan(plan_id)
        if plan.status != "finalized" or not plan.sha256:
            raise ValueError("only a finalized plan can be applied")
        capability = self.storage.write_capability()
        if not capability.get("write_enabled"):
            raise PermissionError("write capability is disabled; run the write probe first")
        export = self.storage.load_export(plan.export_id)
        songs_by_mid = {song["mid"]: song for song in export["songs"]}
        _, current_songs = await self.client.export_liked()
        current_by_mid = {song.mid: song for song in current_songs}
        unavailable = set(songs_by_mid) - set(current_by_mid)
        if unavailable:
            raise RuntimeError(f"{len(unavailable)} source songs are no longer available in the liked playlist")
        taxonomy = {item.key: item for item in plan.taxonomy}
        targets: dict[str, list[str]] = {key: [] for key in taxonomy}
        for assignment in plan.assignments.values():
            for key in assignment.targets:
                targets[key].append(assignment.song_mid)

        run = RunRecord(id="run-" + secrets.token_hex(6), plan_id=plan.id, plan_sha256=plan.sha256)
        self.storage.save_run(run)
        try:
            playlists = await self.client.list_created_playlists()
            by_name: dict[str, list[Any]] = {}
            for playlist in playlists:
                by_name.setdefault(playlist.name.casefold(), []).append(playlist)
            for key, mids in targets.items():
                if not mids:
                    continue
                name = taxonomy[key].name
                matches = by_name.get(name.casefold(), [])
                if len(matches) > 1:
                    raise RuntimeError(f"multiple created playlists have the exact name: {name}")
                if matches:
                    target = matches[0]
                    if target.directory_id == 201:
                        raise RuntimeError("the liked playlist can never be a write target")
                    if target.playlist_id is None:
                        raise RuntimeError(f"target playlist is not readable: {name}")
                    directory_id = target.directory_id
                    existing = {song.mid for song in await self.client.get_playlist_songs(target.playlist_id)}
                else:
                    directory_id = await self.client.create_playlist(name)
                    run.created_playlists[key] = directory_id
                    run.added_songs[key] = []
                    self.storage.save_run(run)
                    existing = set()
                pending = [mid for mid in mids if mid not in existing]
                run.added_songs.setdefault(key, [])
                for start in range(0, len(pending), 20):
                    batch = pending[start : start + 20]
                    await self.client.add_songs(directory_id, [current_by_mid[mid] for mid in batch])
                    refreshed = next(
                        (item for item in await self.client.list_created_playlists() if item.directory_id == directory_id),
                        None,
                    )
                    if refreshed is None or refreshed.playlist_id is None:
                        raise RuntimeError(f"playlist could not be read after adding songs: {name}")
                    observed = {song.mid for song in await self.client.get_playlist_songs(refreshed.playlist_id)}
                    missing = set(batch) - observed
                    if missing:
                        raise RuntimeError(f"QQ Music did not confirm {len(missing)} added songs in {name}")
                    run.added_songs[key].extend(batch)
                    self.storage.save_run(run)
            run.status = "completed"
            run.completed_at = utc_now()
            self.storage.save_run(run)
        except Exception as error:
            run.status = "failed"
            run.errors.append(f"{type(error).__name__}: {error}")
            run.completed_at = utc_now()
            self.storage.save_run(run)
            raise
        return {
            "run_id": run.id,
            "status": run.status,
            "created_playlists": len(run.created_playlists),
            "added_songs": sum(len(items) for items in run.added_songs.values()),
            "log_path": str(self.storage.runs / f"{run.id}.json"),
            "source_song_count": len(songs_by_mid),
        }

    async def rollback_run(self, run_id: str) -> dict[str, Any]:
        run = self.storage.load_run(run_id)
        if run.status not in ("completed", "failed"):
            raise ValueError("only completed or failed runs can be rolled back")
        plan = self.storage.load_plan(run.plan_id)
        _, current_songs = await self.client.export_liked()
        songs_by_mid = {song.mid: song for song in current_songs}
        playlists = {item.directory_id: item for item in await self.client.list_created_playlists()}
        failures: list[str] = []
        for key, mids in run.added_songs.items():
            category_failed = False
            directory_id = run.created_playlists.get(key)
            if directory_id is None:
                category = next((item for item in plan.taxonomy if item.key == key), None)
                matches = [item for item in playlists.values() if category and item.name.casefold() == category.name.casefold()]
                if len(matches) != 1:
                    failures.append(f"could not uniquely identify existing playlist for {key}")
                    continue
                directory_id = matches[0].directory_id
            songs = [songs_by_mid[mid] for mid in mids if mid in songs_by_mid]
            if len(songs) != len(mids):
                failures.append(f"could not resolve all songs for {key}")
                category_failed = True
            for start in range(0, len(songs), 20):
                try:
                    await self.client.remove_songs(directory_id, songs[start : start + 20])
                except Exception as error:
                    failures.append(f"remove {key}: {type(error).__name__}")
                    category_failed = True
            if key in run.created_playlists and not category_failed:
                try:
                    refreshed = next(
                        (item for item in await self.client.list_created_playlists() if item.directory_id == directory_id),
                        None,
                    )
                    if refreshed is None:
                        continue
                    if refreshed.playlist_id is None:
                        raise RuntimeError("new playlist cannot be read before deletion")
                    remaining = await self.client.get_playlist_songs(refreshed.playlist_id)
                    if remaining:
                        raise RuntimeError("new playlist contains songs outside this run; deletion refused")
                    await self.client.delete_playlist(directory_id)
                except Exception as error:
                    failures.append(f"delete {key}: {type(error).__name__}")
        if failures:
            run.errors.extend(failures)
            self.storage.save_run(run)
            raise RuntimeError("rollback incomplete; inspect the run log")
        run.status = "rolled_back"
        run.completed_at = utc_now()
        self.storage.save_run(run)
        return {"run_id": run.id, "status": run.status}

    @staticmethod
    def _require_draft(plan: OrganizationPlan) -> None:
        if plan.status != "draft":
            raise ValueError("finalized plans are immutable; create a revision instead")
