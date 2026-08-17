from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from qqmusic_organizer.models import Assignment, SmartPlaylistBucket, SmartPlaylistRule, SongRecord, TaxonomyItem
from qqmusic_organizer.organizer import Organizer
from qqmusic_organizer.qqmusic import ProtocolChangedError, QQMusicClient, normalized_uin, parse_cookie
from qqmusic_organizer.server import build_mcp
from qqmusic_organizer.session_cache import delete_cookie, load_cookie, save_cookie
from qqmusic_organizer.storage import Storage


def response(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


@pytest.mark.anyio
async def test_gb18030_json_response_is_supported() -> None:
    raw = json.dumps({"code": 0, "message": "成功"}, ensure_ascii=False).encode("gb18030")
    client = QQMusicClient(
        "uin=o12345678; qm_keyst=x",
        httpx.MockTransport(lambda _: httpx.Response(200, content=raw, headers={"content-type": "application/json"})),
    )
    try:
        assert (await client._json("GET", "https://example.test"))["message"] == "成功"
    finally:
        await client.close()


@pytest.mark.anyio
async def test_export_liked_and_never_persist_cookie(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["cookie"] == "uin=o12345678; qm_keyst=secret-value"
        if "fcg_user_created_diss" in str(request.url):
            return response(
                {
                    "code": 0,
                    "data": {
                        "disslist": [
                            {"dirid": 201, "tid": 999, "diss_name": "我喜欢", "song_cnt": 2}
                        ]
                    },
                }
            )
        return response(
            {
                "code": 0,
                "cdlist": [
                    {
                        "songnum": 2,
                        "songlist": [
                            {
                                "id": 1,
                                "mid": "MID1",
                                "name": "夜曲",
                                "singer": [{"name": "周杰伦"}],
                                "album": {"name": "十一月的萧邦", "mid": "ALB1"},
                                "interval": 226,
                            },
                            {
                                "id": 2,
                                "mid": "MID2",
                                "name": "晴天",
                                "singer": [{"name": "周杰伦"}],
                                "album": {"name": "叶惠美", "mid": "ALB2"},
                                "interval": 269,
                            },
                        ],
                    }
                ],
            }
        )

    storage = Storage(tmp_path)
    client = QQMusicClient("uin=o12345678; qm_keyst=secret-value", httpx.MockTransport(handler))
    try:
        result = await Organizer(client, storage).export_liked()
    finally:
        await client.close()
    assert result["count"] == 2
    assert (Path(result["path"]) / "liked.csv").exists()
    all_files = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in tmp_path.rglob("*") if path.is_file())
    assert "secret-value" not in all_files


@pytest.mark.anyio
async def test_incomplete_export_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "fcg_user_created_diss" in str(request.url):
            return response({"code": 0, "data": {"disslist": [{"dirid": 201, "tid": 999, "song_cnt": 2}]}})
        return response({"code": 0, "cdlist": [{"songnum": 2, "songlist": [{"id": 1, "mid": "MID1", "name": "One"}]}]})

    client = QQMusicClient("uin=o12345678; qm_keyst=x", httpx.MockTransport(handler))
    with pytest.raises(ProtocolChangedError, match="only 1 of 2"):
        await client.export_liked()
    await client.close()


def test_cookie_validation() -> None:
    assert parse_cookie("uin=o123; qm_keyst=abc") == {"uin": "o123", "qm_keyst": "abc"}
    with pytest.raises(ValueError):
        parse_cookie("uin=o123\nqm_keyst=abc")
    assert normalized_uin({"login_type": "2", "uin": "o111", "wxuin": "222"}) == "222"


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_dpapi_cookie_cache_is_encrypted_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "session.dpapi"
    raw = "uin=o12345678; qm_keyst=secret-value"
    save_cookie(path, raw)
    assert raw.encode() not in path.read_bytes()
    assert load_cookie(path) == raw
    delete_cookie(path)
    assert load_cookie(path) is None


def create_export(storage: Storage) -> str:
    export_id, _ = storage.save_export(
        [
            SongRecord(mid="MID1", song_id=1, name="One", singers=["A"]),
            SongRecord(mid="MID2", song_id=2, name="Two", singers=["B"]),
        ]
    )
    return export_id


def test_plan_constraints_finalize_and_hash_integrity(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    organizer = Organizer(client=None, storage=storage)  # type: ignore[arg-type]
    plan = organizer.create_plan(create_export(storage))
    plan = organizer.set_taxonomy(plan.id, [TaxonomyItem(key="night", name="夜晚")])
    with pytest.raises(ValidationError, match="low-confidence"):
        Assignment(song_mid="MID1", targets=["night"], confidence=0.5, reason="不确定")
    organizer.upsert_assignments(
        plan.id,
        [
            Assignment(song_mid="MID1", targets=["night"], confidence=0.9, reason="安静的夜晚氛围"),
            Assignment(song_mid="MID2", targets=["needs_review"], confidence=0.5, reason="信息不足"),
        ],
    )
    finalized = organizer.finalize(plan.id)
    assert finalized.sha256 and len(finalized.sha256) == 64
    with pytest.raises(ValueError, match="immutable"):
        organizer.set_taxonomy(plan.id, [TaxonomyItem(key="pop", name="流行")])

    path = storage.plans / f"{finalized.id}.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["assignments"]["MID1"]["reason"] = "被修改"
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="hash verification"):
        storage.load_plan(finalized.id)


class FakeWriteClient:
    uin = "12345678"

    def __init__(self) -> None:
        from qqmusic_organizer.qqmusic import Playlist

        self.playlists = [Playlist(201, 999, "我喜欢", 2), Playlist(301, 9001, "夜晚", 1)]
        self.added: list[tuple[int, list[str]]] = []
        self.created: list[str] = []
        self.contents: dict[int, set[str]] = {999: {"MID1", "MID2"}, 9001: {"MID1"}}
        self.removed: list[tuple[int, list[int]]] = []
        self.deleted: list[int] = []
        self.liked_songs = [
            SongRecord(mid="MID1", song_id=1, song_type=0, name="MID1"),
            SongRecord(mid="MID2", song_id=2, song_type=1, name="MID2"),
        ]
        self.detail_calls = 0

    async def list_created_playlists(self):
        return list(self.playlists)

    async def get_playlist_songs(self, playlist_id: int):
        return [SongRecord(mid=mid, song_id=1, song_type=0, name=mid) for mid in self.contents.get(playlist_id, set())]

    async def export_liked(self):
        from qqmusic_organizer.qqmusic import Playlist

        return Playlist(201, 999, "我喜欢", len(self.liked_songs)), list(self.liked_songs)

    async def get_song_detail(self, mid: str):
        self.detail_calls += 1
        return {
            "track_info": {
                "mid": mid,
                "title": f"{mid} Live",
                "singer": [{"name": "测试歌手"}],
                "album": {"name": "测试专辑"},
                "time_public": "2025-01-01",
                "language": "国语",
            }
        }

    async def get_lyrics(self, mid: str):
        return {"lyric": f"[00:01.00]{mid} 歌词"}

    async def create_playlist(self, name: str):
        from qqmusic_organizer.qqmusic import Playlist

        self.created.append(name)
        directory_id = 400 + len(self.created)
        playlist_id = 9100 + len(self.created)
        self.playlists.append(Playlist(directory_id, playlist_id, name, 0))
        self.contents[playlist_id] = set()
        return directory_id

    async def add_songs(self, directory_id: int, songs: list[SongRecord]):
        mids = [song.mid for song in songs]
        self.added.append((directory_id, mids))
        playlist = next(item for item in self.playlists if item.directory_id == directory_id)
        self.contents[playlist.playlist_id or 0].update(mids)

    async def remove_songs(self, directory_id: int, songs: list[SongRecord]):
        self.removed.append((directory_id, [song.song_id or 0 for song in songs]))
        playlist = next(item for item in self.playlists if item.directory_id == directory_id)
        self.contents[playlist.playlist_id or 0].difference_update(song.mid for song in songs)

    async def delete_playlist(self, directory_id: int):
        self.deleted.append(directory_id)
        playlist = next(item for item in self.playlists if item.directory_id == directory_id)
        self.playlists.remove(playlist)
        self.contents.pop(playlist.playlist_id or 0, None)


@pytest.mark.anyio
async def test_apply_reuses_exact_name_and_skips_existing(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    client = FakeWriteClient()
    organizer = Organizer(client=client, storage=storage)  # type: ignore[arg-type]
    plan = organizer.create_plan(create_export(storage))
    organizer.set_taxonomy(plan.id, [TaxonomyItem(key="night", name="夜晚")])
    organizer.upsert_assignments(
        plan.id,
        [
            Assignment(song_mid="MID1", targets=["night"], confidence=0.9, reason="已有"),
            Assignment(song_mid="MID2", targets=["night", "needs_review"], confidence=0.9, reason="交叉分类"),
        ],
    )
    finalized = organizer.finalize(plan.id)
    storage.set_write_capability(True, "test")
    result = await organizer.apply_plan(finalized.id)
    assert result["status"] == "completed"
    assert (301, ["MID2"]) in client.added
    assert "待整理" in client.created
    run = json.loads((storage.runs / f"{result['run_id']}.json").read_text(encoding="utf-8"))
    assert run["status"] == "completed"

    run_model = storage.load_run(result["run_id"])
    rolled_back = await organizer.rollback_run(run_model.id)
    assert rolled_back["status"] == "rolled_back"
    assert client.removed
    assert client.deleted


@pytest.mark.anyio
async def test_generic_playlist_tools_are_safe_and_logged(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    client = FakeWriteClient()
    organizer = Organizer(client=client, storage=storage)  # type: ignore[arg-type]
    storage.set_write_capability(True, "test")

    created = await organizer.create_playlist("通用测试")
    directory_id = created["directory_id"]
    song = SongRecord(mid="MID3", song_id=3, song_type=0, name="Three")
    added = await organizer.add_songs(directory_id, [song])
    assert added["added"] == 1
    assert (storage.operations / Path(added["operation_log"]).name).exists()
    removed = await organizer.remove_songs(directory_id, [song])
    assert removed["removed"] == 1
    deleted = await organizer.delete_playlist(directory_id)
    assert deleted["deleted"] is True

    with pytest.raises(PermissionError, match="liked playlist"):
        await organizer.add_songs(201, [song])
    with pytest.raises(ValueError, match="non-empty"):
        await organizer.delete_playlist(301)


@pytest.mark.anyio
async def test_library_analysis_and_smart_workflows(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    client = FakeWriteClient()
    organizer = Organizer(client=client, storage=storage)  # type: ignore[arg-type]
    storage.set_write_capability(True, "test")

    analysis = await organizer.analyze_library()
    assert analysis["liked_song_count"] == 2
    assert analysis["unorganized_liked_count"] == 1
    assert analysis["duplicate_group_count"] == 1

    merged = await organizer.merge_playlists("合并", [201, 301])
    assert merged["source_song_count"] == 2
    split = await organizer.split_playlist(
        201,
        [
            SmartPlaylistBucket(name="一", song_mids=["MID1"]),
            SmartPlaylistBucket(name="二", song_mids=["MID2"]),
        ],
    )
    assert len(split["created"]) == 2
    with pytest.raises(ValueError, match="multiple buckets"):
        await organizer.split_playlist(201, [SmartPlaylistBucket(name="重复", song_mids=["MID1"]), SmartPlaylistBucket(name="重复2", song_mids=["MID1"])])


@pytest.mark.anyio
async def test_smart_playlist_sync_requires_matching_preview(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    client = FakeWriteClient()
    organizer = Organizer(client=client, storage=storage)  # type: ignore[arg-type]
    storage.set_write_capability(True, "test")

    preview = await organizer.preview_smart_playlist_sync(
        "夜晚",
        201,
        SmartPlaylistRule(keyword="MID"),
    )
    assert preview["add_count"] == 1
    assert preview["remove_count"] == 0
    result = await organizer.apply_smart_playlist_sync(
        preview["smart_playlist_id"],
        preview["preview_sha256"],
    )
    assert result["added"] == 1
    assert client.contents[9001] == {"MID1", "MID2"}

    stale = await organizer.preview_smart_playlist_sync(
        "夜晚",
        201,
        SmartPlaylistRule(keyword="MID"),
        remove_extraneous=True,
    )
    client.contents[9001].add("UNEXPECTED")
    with pytest.raises(RuntimeError, match="changed after preview"):
        await organizer.apply_smart_playlist_sync(stale["smart_playlist_id"], stale["preview_sha256"])


@pytest.mark.anyio
async def test_incremental_export_is_plan_ready(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    baseline_id, _ = storage.save_export([SongRecord(mid="MID1", song_id=1, name="MID1")])
    client = FakeWriteClient()
    organizer = Organizer(client=client, storage=storage)  # type: ignore[arg-type]

    result = await organizer.prepare_incremental_organization(baseline_id)
    assert result["added_count"] == 1
    assert result["removed_count"] == 0
    delta = storage.load_export(result["incremental_export_id"])
    assert delta["snapshot_type"] == "incremental"
    assert [song["mid"] for song in delta["songs"]] == ["MID2"]
    assert organizer.create_plan(result["incremental_export_id"]).export_id == result["incremental_export_id"]


@pytest.mark.anyio
async def test_export_metadata_enrichment_uses_cache(tmp_path: Path) -> None:
    storage = Storage(tmp_path)
    export_id, _ = storage.save_export([SongRecord(mid="MID1", song_id=1, name="MID1")])
    client = FakeWriteClient()
    organizer = Organizer(client=client, storage=storage)  # type: ignore[arg-type]

    first = await organizer.enrich_export_page(export_id, include_lyrics=True)
    second = await organizer.enrich_export_page(export_id, include_lyrics=True)
    assert client.detail_calls == 1
    assert first["songs"][0]["language"] == "国语"
    assert first["songs"][0]["release_year"] == 2025
    assert first["songs"][0]["tags"] == ["live"]
    assert first["songs"][0]["lyrics_excerpt"] == "MID1 歌词"
    assert second["songs"][0]["mid"] == "MID1"


@pytest.mark.anyio
async def test_mcp_tools_expose_safety_annotations(tmp_path: Path) -> None:
    organizer = Organizer(client=FakeWriteClient(), storage=Storage(tmp_path))  # type: ignore[arg-type]
    tools = {tool.name: tool for tool in await build_mcp(organizer).list_tools()}
    assert len(tools) == 34
    assert tools["qqmusic_status"].annotations.readOnlyHint is True
    assert tools["qqmusic_create_playlist"].annotations.destructiveHint is False
    assert tools["qqmusic_apply_smart_playlist_sync"].annotations.destructiveHint is True


@pytest.mark.anyio
async def test_modern_musicu_write_request_shapes() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload["req_0"])
        method = payload["req_0"]["method"]
        data = {"result": {"dirId": 321}} if method == "AddPlaylist" else {"result": 1}
        return response({"code": 0, "req_0": {"code": 0, "data": data}})

    client = QQMusicClient("uin=o12345678; qm_keyst=x", httpx.MockTransport(handler))
    song = SongRecord(mid="MID1", song_id=456, song_type=7, name="One")
    try:
        assert await client.create_playlist("测试") == 321
        await client.add_songs(321, [song])
        await client.remove_songs(321, [song])
        await client.delete_playlist(321)
    finally:
        await client.close()

    assert [(item["module"], item["method"]) for item in requests] == [
        ("music.musicasset.PlaylistBaseWrite", "AddPlaylist"),
        ("music.musicasset.PlaylistDetailWrite", "AddSonglist"),
        ("music.musicasset.PlaylistDetailWrite", "DelSonglist"),
        ("music.musicasset.PlaylistBaseWrite", "DelPlaylist"),
    ]
    assert requests[1]["param"] == {"dirId": 321, "v_songInfo": [{"songType": 7, "songId": 456}]}
    with pytest.raises(ValueError, match="never be deleted"):
        await client.delete_playlist(201)


@pytest.mark.anyio
async def test_search_detail_and_lyrics_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "client_search_cp" in str(request.url):
            return response({"code": 0, "data": {"song": {"totalnum": 1, "list": [{"id": 7, "mid": "MID7", "name": "搜索结果", "singer": [{"name": "歌手"}], "album": {"name": "专辑", "mid": "ALB7"}}]}}})
        if "lyric/fcgi-bin" in str(request.url):
            return response({"code": 0, "lyric": "[00:01.00]歌词"})
        payload = json.loads(request.content)
        assert payload["req_0"]["module"] == "music.pf_song_detail_svr"
        return response({"code": 0, "req_0": {"code": 0, "data": {"track_info": {"mid": "MID7", "title": "详情"}}}})

    client = QQMusicClient("uin=o12345678; qm_keyst=x", httpx.MockTransport(handler))
    try:
        found = await client.search_songs("搜索结果")
        assert found["total"] == 1 and found["songs"][0]["mid"] == "MID7"
        assert (await client.get_song_detail("MID7"))["title"] == "详情"
        assert (await client.get_lyrics("MID7"))["lyric"] == "[00:01.00]歌词"
    finally:
        await client.close()
