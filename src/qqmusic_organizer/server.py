from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .models import Assignment, SmartPlaylistBucket, SmartPlaylistRule, SongRecord, TaxonomyItem
from .organizer import Organizer


def build_mcp(organizer: Organizer) -> FastMCP:
    mcp = FastMCP("qqmusic-mcp", json_response=True)

    @mcp.tool()
    async def qqmusic_status() -> dict[str, Any]:
        """Check the local QQ Music session and available organizer capabilities."""
        return await organizer.status()

    @mcp.tool()
    async def qqmusic_export_liked() -> dict[str, Any]:
        """Export the complete QQ Music liked playlist to local JSON and CSV snapshots."""
        return await organizer.export_liked()

    @mcp.tool()
    async def qqmusic_list_playlists() -> list[dict[str, Any]]:
        """List all created QQ Music playlists, including the read-only liked playlist."""
        return await organizer.list_playlists()

    @mcp.tool()
    async def qqmusic_get_playlist(directory_id: int) -> dict[str, Any]:
        """Read one playlist and its song metadata. Reading dirId 201 is allowed."""
        return await organizer.get_playlist(directory_id)

    @mcp.tool()
    async def qqmusic_create_playlist(name: str) -> dict[str, Any]:
        """Create an empty playlist after a successful write probe."""
        return await organizer.create_playlist(name)

    @mcp.tool()
    async def qqmusic_add_songs(directory_id: int, songs: list[SongRecord]) -> dict[str, Any]:
        """Add 1-20 songs to a self-created playlist and verify them by reading back."""
        return await organizer.add_songs(directory_id, songs)

    @mcp.tool()
    async def qqmusic_remove_songs(directory_id: int, songs: list[SongRecord]) -> dict[str, Any]:
        """Remove 1-20 songs from a self-created playlist and verify the result."""
        return await organizer.remove_songs(directory_id, songs)

    @mcp.tool()
    async def qqmusic_delete_playlist(directory_id: int) -> dict[str, Any]:
        """Delete an empty self-created playlist; dirId 201 and non-empty playlists are refused."""
        return await organizer.delete_playlist(directory_id)

    @mcp.tool()
    async def qqmusic_analyze_library() -> dict[str, Any]:
        """Analyze playlist coverage, duplicates, empty playlists, and unorganized liked songs."""
        return await organizer.analyze_library()

    @mcp.tool()
    async def qqmusic_find_duplicates() -> dict[str, Any]:
        """Find songs that occur in multiple playlists."""
        return await organizer.find_duplicates()

    @mcp.tool()
    async def qqmusic_find_empty_playlists() -> dict[str, Any]:
        """Find empty self-created playlists without changing anything."""
        return await organizer.find_empty_playlists()

    @mcp.tool()
    async def qqmusic_find_unorganized_songs() -> dict[str, Any]:
        """Find liked songs that are absent from every other playlist."""
        return await organizer.find_unorganized_songs()

    @mcp.tool()
    async def qqmusic_compare_playlists(left_directory_id: int, right_directory_id: int) -> dict[str, Any]:
        """Compare two playlists and return their intersection and side-specific songs."""
        return await organizer.compare_playlists(left_directory_id, right_directory_id)

    @mcp.tool()
    async def qqmusic_create_smart_playlist(name: str, source_directory_id: int, rule: SmartPlaylistRule) -> dict[str, Any]:
        """Create a playlist from a source playlist using metadata and duration filters."""
        return await organizer.create_smart_playlist(name, source_directory_id, rule)

    @mcp.tool()
    async def qqmusic_merge_playlists(name: str, source_directory_ids: list[int]) -> dict[str, Any]:
        """Merge 2-20 playlists into a new deduplicated playlist; sources are preserved."""
        return await organizer.merge_playlists(name, source_directory_ids)

    @mcp.tool()
    async def qqmusic_split_playlist(source_directory_id: int, buckets: list[SmartPlaylistBucket]) -> dict[str, Any]:
        """Split a playlist into new buckets selected by the AI; the source is preserved."""
        return await organizer.split_playlist(source_directory_id, buckets)

    @mcp.tool()
    async def qqmusic_search(query: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Search QQ Music songs by title, artist, or album."""
        return await organizer.client.search_songs(query, page, page_size)

    @mcp.tool()
    async def qqmusic_get_song_detail(mid: str) -> dict[str, Any]:
        """Read QQ Music metadata for one song MID."""
        return await organizer.client.get_song_detail(mid)

    @mcp.tool()
    async def qqmusic_get_lyrics(mid: str) -> dict[str, str]:
        """Read lyrics for one song MID when QQ Music provides them."""
        return await organizer.client.get_lyrics(mid)

    @mcp.tool()
    def qqmusic_get_export_summary(export_id: str) -> dict[str, Any]:
        """Read aggregate metadata for a previously exported liked library."""
        return organizer.export_summary(export_id)

    @mcp.tool()
    def qqmusic_get_export_page(export_id: str, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        """Read one page of song metadata from a local liked-library export."""
        return organizer.export_page(export_id, page, page_size)

    @mcp.tool()
    def qqmusic_create_plan(export_id: str) -> dict[str, Any]:
        """Create a draft organization plan for an export."""
        return organizer.create_plan(export_id).model_dump(mode="json")

    @mcp.tool()
    def qqmusic_set_taxonomy(plan_id: str, items: list[TaxonomyItem]) -> dict[str, Any]:
        """Replace the categories on a draft plan; needs_review is added automatically."""
        return organizer.set_taxonomy(plan_id, items).model_dump(mode="json")

    @mcp.tool()
    def qqmusic_upsert_assignments(plan_id: str, assignments: list[Assignment]) -> dict[str, Any]:
        """Add or update at most 200 song assignments on a draft plan."""
        return organizer.upsert_assignments(plan_id, assignments).model_dump(mode="json")

    @mcp.tool()
    def qqmusic_preview_plan(plan_id: str) -> dict[str, Any]:
        """Return coverage and playlist counts for a plan without changing QQ Music."""
        return organizer.preview(plan_id)

    @mcp.tool()
    def qqmusic_finalize_plan(plan_id: str) -> dict[str, Any]:
        """Freeze a complete plan and calculate its immutable SHA-256."""
        return organizer.finalize(plan_id).model_dump(mode="json")

    @mcp.tool()
    def qqmusic_revise_plan(plan_id: str) -> dict[str, Any]:
        """Create an editable revision from a finalized plan."""
        return organizer.revise(plan_id).model_dump(mode="json")

    @mcp.tool()
    async def qqmusic_probe_write() -> dict[str, Any]:
        """Run a create/add/read/remove/delete probe using a temporary playlist."""
        return await organizer.probe_write()

    @mcp.tool()
    async def qqmusic_apply_plan(plan_id: str) -> dict[str, Any]:
        """Apply a finalized plan after a successful capability probe."""
        return await organizer.apply_plan(plan_id)

    @mcp.tool()
    async def qqmusic_rollback_run(run_id: str) -> dict[str, Any]:
        """Remove only songs recorded as added by an organizer run."""
        return await organizer.rollback_run(run_id)

    return mcp
