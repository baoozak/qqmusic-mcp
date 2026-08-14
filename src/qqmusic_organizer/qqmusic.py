from __future__ import annotations

import re
import json
import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from .models import SongRecord


class QQMusicError(RuntimeError):
    pass


class AuthenticationError(QQMusicError):
    pass


class ProtocolChangedError(QQMusicError):
    pass


@dataclass(frozen=True)
class Playlist:
    directory_id: int
    playlist_id: int | None
    name: str
    song_count: int


def parse_cookie(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    if "\r" in raw or "\n" in raw:
        raise ValueError("cookie must be a single line")
    for part in raw.split(";"):
        name, separator, value = part.strip().partition("=")
        if not separator or not name:
            continue
        if not re.fullmatch(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", name):
            raise ValueError("cookie contains an invalid name")
        cookies[name] = value
    if not cookies:
        raise ValueError("no cookies were found")
    return cookies


def normalized_uin(cookies: dict[str, str]) -> str:
    if cookies.get("login_type") == "2":
        raw = cookies.get("wxuin") or cookies.get("uin") or ""
    else:
        raw = cookies.get("uin") or cookies.get("wxuin") or cookies.get("p_uin") or ""
    value = raw.lstrip("o")
    return value if value.isdigit() else ""


def gtk(skey: str) -> int:
    value = 5381
    for char in skey:
        value += (value << 5) + ord(char)
    return value & 0x7FFFFFFF


class QQMusicClient:
    def __init__(self, raw_cookie: str, transport: httpx.AsyncBaseTransport | None = None):
        self.cookies = parse_cookie(raw_cookie)
        self.uin = normalized_uin(self.cookies)
        self.g_tk = gtk(self.cookies.get("p_skey") or self.cookies.get("skey") or "")
        self.http = httpx.AsyncClient(
            cookies=self.cookies,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                "Referer": "https://y.qq.com/",
                "Origin": "https://y.qq.com",
            },
            timeout=30,
            follow_redirects=True,
            transport=transport,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def __aenter__(self) -> QQMusicClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def _json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            for attempt in range(3):
                response = await self.http.request(method, url, **kwargs)
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt == 2:
                    response.raise_for_status()
                await asyncio.sleep(0.5 * (2**attempt))
            response.raise_for_status()
            text = None
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    text = response.content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                raise ValueError("QQ Music response uses an unsupported encoding")
            try:
                payload = json.loads(text)
            except ValueError:
                match = re.search(r"\((\{.*\})\)", text, re.DOTALL)
                if not match:
                    raise
                payload = json.loads(match.group(1))
        except (httpx.HTTPError, ValueError) as error:
            raise QQMusicError(f"QQ Music request failed: {type(error).__name__}") from error
        if not isinstance(payload, dict):
            raise ProtocolChangedError("QQ Music returned an unexpected response")
        if payload.get("code") in (1, 1000) or payload.get("result") == 301:
            raise AuthenticationError("QQ Music login has expired")
        return payload

    async def _musicu(self, module: str, method: str, param: dict[str, Any]) -> dict[str, Any]:
        payload = await self._json(
            "POST",
            "https://u.y.qq.com/cgi-bin/musicu.fcg",
            params={"format": "json", "g_tk": self.g_tk},
            json={
                "comm": {"ct": 24, "cv": 0, "uin": self.uin},
                "req_0": {"module": module, "method": method, "param": param},
            },
        )
        result = payload.get("req_0")
        if not isinstance(result, dict):
            raise ProtocolChangedError("musicu response no longer contains req_0")
        if result.get("code") in (1000, -1000):
            raise AuthenticationError("QQ Music login has expired")
        if result.get("code") != 0:
            raise QQMusicError(f"{method} failed: code={result.get('code')}")
        return result

    async def list_created_playlists(self) -> list[Playlist]:
        if not self.uin:
            raise AuthenticationError("cookie does not contain a usable QQ Music account id")
        payload = await self._json(
            "GET",
            "https://c.y.qq.com/rsc/fcgi-bin/fcg_user_created_diss",
            params={
                "hostuin": self.uin,
                "sin": 0,
                "size": 200,
                "g_tk": self.g_tk,
                "loginUin": self.uin,
                "format": "json",
                "inCharset": "utf8",
                "outCharset": "utf-8",
                "platform": "yqq.json",
            },
        )
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("disslist"), list):
            raise ProtocolChangedError("created-playlist response no longer contains data.disslist")
        playlists = []
        for item in data["disslist"]:
            if not isinstance(item, dict):
                continue
            directory_id = item.get("dirid")
            if not isinstance(directory_id, int):
                continue
            playlist_id = item.get("tid") or item.get("dissid")
            playlists.append(
                Playlist(
                    directory_id=directory_id,
                    playlist_id=int(playlist_id) if str(playlist_id).isdigit() else None,
                    name=str(item.get("diss_name") or item.get("dissname") or ""),
                    song_count=int(item.get("song_cnt") or item.get("songnum") or 0),
                )
            )
        return playlists

    async def liked_playlist(self) -> Playlist:
        playlists = await self.list_created_playlists()
        liked = next((playlist for playlist in playlists if playlist.directory_id == 201), None)
        if liked is None:
            raise ProtocolChangedError("could not locate the QQ Music liked playlist (dirid 201)")
        if liked.playlist_id is None:
            raise ProtocolChangedError("liked playlist has no readable playlist id")
        return liked

    async def get_playlist_songs(self, playlist_id: int) -> list[SongRecord]:
        payload = await self._json(
            "GET",
            "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg",
            params={
                "type": 1,
                "utf8": 1,
                "onlysong": 0,
                "disstid": playlist_id,
                "loginUin": self.uin,
                "format": "json",
                "inCharset": "utf8",
                "outCharset": "utf-8",
            },
        )
        cdlist = payload.get("cdlist")
        if not isinstance(cdlist, list) or not cdlist or not isinstance(cdlist[0], dict):
            raise ProtocolChangedError("playlist response no longer contains cdlist")
        raw_songs = cdlist[0].get("songlist")
        if not isinstance(raw_songs, list):
            raise ProtocolChangedError("playlist response no longer contains songlist")
        songs: list[SongRecord] = []
        seen: set[str] = set()
        for item in raw_songs:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("mid") or item.get("songmid") or "")
            name = str(item.get("name") or item.get("songname") or "")
            if not mid or not name or mid in seen:
                continue
            seen.add(mid)
            album = item.get("album") if isinstance(item.get("album"), dict) else {}
            singer_items = item.get("singer") if isinstance(item.get("singer"), list) else []
            songs.append(
                SongRecord(
                    mid=mid,
                    song_id=item.get("id") or item.get("songid"),
                    song_type=int(item.get("type") or item.get("songtype") or 0),
                    name=name,
                    singers=[str(singer.get("name") or singer.get("singername")) for singer in singer_items if isinstance(singer, dict) and (singer.get("name") or singer.get("singername"))],
                    album=str(album.get("name") or item.get("albumname") or ""),
                    album_mid=str(album.get("mid") or item.get("albummid") or ""),
                    duration_seconds=int(item.get("interval") or 0),
                )
            )
        declared_count = int(cdlist[0].get("songnum") or len(raw_songs))
        if declared_count and not songs:
            raise ProtocolChangedError("playlist declared songs but none could be parsed")
        return songs

    async def search_songs(self, query: str, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        if not query.strip() or len(query) > 100:
            raise ValueError("search query must contain 1-100 characters")
        if page < 1 or page_size < 1 or page_size > 50:
            raise ValueError("page must be >= 1 and page_size must be 1-50")
        payload = await self._json(
            "GET",
            "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
            params={
                "w": query,
                "p": page,
                "n": page_size,
                "format": "json",
                "t": 0,
                "cr": 1,
                "catZhida": 1,
                "lossless": 1,
                "flag_qc": 0,
                "remoteplace": "txt.yqq.song",
                "g_tk": self.g_tk,
            },
        )
        data = payload.get("data")
        song_data = data.get("song") if isinstance(data, dict) else None
        raw_songs = song_data.get("list") if isinstance(song_data, dict) else None
        if not isinstance(raw_songs, list):
            raise ProtocolChangedError("search response no longer contains data.song.list")
        songs = [self._song_from_item(item) for item in raw_songs if isinstance(item, dict)]
        return {
            "query": query,
            "page": page,
            "page_size": page_size,
            "total": int(song_data.get("totalnum") or len(songs)),
            "songs": [song.model_dump(mode="json") for song in songs],
        }

    async def get_song_detail(self, mid: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9A-Za-z]{1,128}", mid):
            raise ValueError("mid must contain 1-128 ASCII letters or digits")
        payload = await self._musicu(
            "music.pf_song_detail_svr", "get_song_detail_yqq", {"song_mid": mid}
        )
        data = payload.get("data")
        track = data.get("track_info") if isinstance(data, dict) else None
        if not isinstance(track, dict):
            raise ProtocolChangedError("song detail response no longer contains data.track_info")
        return track

    async def get_lyrics(self, mid: str) -> dict[str, str]:
        if not re.fullmatch(r"[0-9A-Za-z]{1,128}", mid):
            raise ValueError("mid must contain 1-128 ASCII letters or digits")
        payload = await self._json(
            "GET",
            "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
            params={"songmid": mid, "format": "json", "nobase64": 1, "g_tk": self.g_tk},
        )
        lyric = payload.get("lyric")
        if not isinstance(lyric, str):
            raise ProtocolChangedError("lyrics response no longer contains lyric")
        return {"mid": mid, "lyric": lyric}

    @staticmethod
    def _song_from_item(item: dict[str, Any]) -> SongRecord:
        album = item.get("album") if isinstance(item.get("album"), dict) else {}
        singers = item.get("singer") if isinstance(item.get("singer"), list) else []
        return SongRecord(
            mid=str(item.get("mid") or item.get("songmid") or ""),
            song_id=item.get("id") or item.get("songid"),
            song_type=int(item.get("type") or item.get("songtype") or 0),
            name=str(item.get("name") or item.get("songname") or ""),
            singers=[str(s.get("name") or s.get("singername")) for s in singers if isinstance(s, dict) and (s.get("name") or s.get("singername"))],
            album=str(album.get("name") or item.get("albumname") or ""),
            album_mid=str(album.get("mid") or item.get("albummid") or ""),
            duration_seconds=int(item.get("interval") or 0),
        )

    async def export_liked(self) -> tuple[Playlist, list[SongRecord]]:
        liked = await self.liked_playlist()
        songs = await self.get_playlist_songs(liked.playlist_id or 0)
        if liked.song_count and len(songs) < liked.song_count:
            raise ProtocolChangedError(
                f"QQ Music returned only {len(songs)} of {liked.song_count} liked songs; export stopped"
            )
        return liked, songs

    async def create_playlist(self, name: str) -> int:
        if not name.strip() or len(name) > 80:
            raise ValueError("playlist name must contain 1-80 characters")
        payload = await self._musicu(
            "music.musicasset.PlaylistBaseWrite",
            "AddPlaylist",
            {"dirName": name, "dirDesc": "", "dirPicUrl": "", "taglist": []},
        )
        data = payload.get("data")
        result = data.get("result") if isinstance(data, dict) else None
        directory_id = result.get("dirId") if isinstance(result, dict) else result
        if not str(directory_id).isdigit():
            raise ProtocolChangedError("AddPlaylist response has no directory id")
        return int(directory_id)

    async def add_songs(self, directory_id: int, songs: list[SongRecord]) -> None:
        if not songs or len(songs) > 20 or any(not song.song_id for song in songs):
            raise ValueError("add_songs requires 1-20 songs with numeric ids")
        await self._musicu(
            "music.musicasset.PlaylistDetailWrite",
            "AddSonglist",
            {"dirId": directory_id, "v_songInfo": [{"songType": song.song_type, "songId": song.song_id} for song in songs]},
        )

    async def remove_songs(self, directory_id: int, songs: list[SongRecord]) -> None:
        if not songs or len(songs) > 20 or any(not song.song_id for song in songs):
            raise ValueError("remove_songs requires 1-20 songs with numeric ids")
        await self._musicu(
            "music.musicasset.PlaylistDetailWrite",
            "DelSonglist",
            {"dirId": directory_id, "v_songInfo": [{"songType": song.song_type, "songId": song.song_id} for song in songs]},
        )

    async def delete_playlist(self, directory_id: int) -> None:
        if directory_id == 201:
            raise ValueError("the liked playlist can never be deleted")
        await self._musicu(
            "music.musicasset.PlaylistBaseWrite",
            "DelPlaylist",
            {"dirId": directory_id},
        )
