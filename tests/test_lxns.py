from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rhythmflow.webui.lxns import (
    LxnsError,
    LxnsReferenceAudioService,
    _download_file,
    is_probably_mp3,
    is_reference_audio_cache_path,
    safe_filename,
)


class LxnsReferenceAudioTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("RHYTHMFLOW_CONFIG_DIR")
        os.environ["RHYTHMFLOW_CONFIG_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("RHYTHMFLOW_CONFIG_DIR", None)
        else:
            os.environ["RHYTHMFLOW_CONFIG_DIR"] = self._prev
        self._tmp.cleanup()

    def test_search_matches_title_artist_and_id(self) -> None:
        service = LxnsReferenceAudioService(fetch_json=lambda _url: {"songs": _songs()})

        self.assertEqual(service.search_songs("maimai", "stellar")[0]["id"], "1001")
        self.assertEqual(service.search_songs("maimai", "sample artist")[0]["id"], "1001")
        self.assertEqual(service.search_songs("maimai", "2002")[0]["id"], "2002")

    def test_chunithm_worlds_end_uses_origin_id(self) -> None:
        service = LxnsReferenceAudioService(fetch_json=lambda _url: {"songs": _songs()})

        song = service.search_songs("chunithm", "worlds end")[0]

        self.assertEqual(song["id"], "3003")
        self.assertEqual(song["asset_song_id"], "8888")
        self.assertIn({"label": "WORLD'S END", "level": "避", "index": 5}, song["difficulties"])

    def test_maimai_difficulty_groups_are_flattened_and_labeled(self) -> None:
        service = LxnsReferenceAudioService(fetch_json=lambda _url: {"songs": [_maimai_grouped_song()]})

        song = service.search_songs("maimai", "grouped")[0]

        self.assertIn({"label": "BASIC", "level": "3", "index": 0}, song["difficulties"])
        self.assertIn({"label": "MASTER", "level": "13", "index": 3}, song["difficulties"])
        self.assertIn({"label": "Re:MASTER", "level": "14", "index": 4}, song["difficulties"])

    def test_chunithm_ultima_is_labeled_from_level_index(self) -> None:
        service = LxnsReferenceAudioService(fetch_json=lambda _url: {"songs": [_chunithm_ultima_song()]})

        song = service.search_songs("chunithm", "ultima")[0]

        self.assertEqual(song["difficulties"], [{"label": "ULTIMA", "level": "14+", "index": 4}])

    def test_download_uses_default_cache_dir(self) -> None:
        calls: list[str] = []

        def download(url: str, target: Path) -> None:
            calls.append(url)
            target.write_bytes(_mp3_bytes())

        service = LxnsReferenceAudioService(
            fetch_json=lambda _url: {"songs": []},
            download_file=download,
        )

        path = Path(service.download_audio("maimai", "1001", "A:/Bad*Title", persist=False))

        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, Path(self._tmp.name) / "reference_audio_cache")
        self.assertNotIn("*", path.name)
        self.assertIn("/maimai/music/1001.mp3", calls[0])

    def test_download_can_persist_to_output_dir(self) -> None:
        output_dir = Path(self._tmp.name) / "output"
        service = LxnsReferenceAudioService(
            fetch_json=lambda _url: {"songs": []},
            download_file=lambda _url, target: target.write_bytes(_mp3_bytes()),
        )

        path = Path(
            service.download_audio(
                "chunithm",
                "8888",
                "World's End",
                persist=True,
                output_dir=str(output_dir),
            )
        )

        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, output_dir)

    def test_safe_filename_handles_windows_reserved_characters(self) -> None:
        self.assertEqual(safe_filename('a<b>c:d"e/f\\g|h?i*j'), "a_b_c_d_e_f_g_h_i_j")

    def test_existing_non_audio_cache_is_replaced(self) -> None:
        target = Path(self._tmp.name) / "reference_audio_cache" / "maimai_1001_Bad.mp3"
        target.parent.mkdir(parents=True)
        target.write_text("<script>challenge</script>", encoding="utf-8")
        service = LxnsReferenceAudioService(
            fetch_json=lambda _url: {"songs": []},
            download_file=lambda _url, path: path.write_bytes(_mp3_bytes()),
        )

        path = Path(service.download_audio("maimai", "1001", "Bad", persist=False))

        self.assertEqual(path, target)
        self.assertTrue(is_probably_mp3(path))

    def test_download_rejects_html_challenge_response(self) -> None:
        service = LxnsReferenceAudioService(
            fetch_json=lambda _url: {"songs": []},
            download_file=lambda _url, target: target.write_text("<script>challenge</script>", encoding="utf-8"),
        )

        with self.assertRaises(LxnsError):
            service.download_audio("maimai", "1001", "Bad", persist=False)

    def test_reference_audio_cache_path_only_matches_own_cache_mp3(self) -> None:
        cache_path = Path(self._tmp.name) / "reference_audio_cache" / "song.mp3"
        other_path = Path(self._tmp.name) / "output" / "song.mp3"
        cache_path.parent.mkdir(parents=True)
        other_path.parent.mkdir(parents=True)
        cache_path.write_bytes(_mp3_bytes())
        other_path.write_bytes(_mp3_bytes())

        self.assertTrue(is_reference_audio_cache_path(cache_path))
        self.assertFalse(is_reference_audio_cache_path(other_path))

    def test_downloader_solves_tencent_edge_cookie_challenge(self) -> None:
        target = Path(self._tmp.name) / "song.mp3"
        calls: list[str] = []

        def fake_urlopen(request, timeout):  # noqa: ANN001 - mirrors urllib callback
            calls.append(request.headers.get("Cookie", ""))
            if len(calls) == 1:
                return _FakeResponse(
                    "text/html",
                    (
                        '<script>function a(a){}'
                        'var e={WTKkN:21896945,bOYDu:350803706,wyeCN:335221809},t=0;'
                        't+="EO_Bot_Ssid=";t=a[_0x649a("0x7")](t,534183936);'
                        'document.cookie="__tst_status="+a(0)+"#;";document.cookie=a(1)+";";'
                        "</script>"
                    ).encode("utf-8"),
                )
            return _FakeResponse("audio/mpeg", _mp3_bytes())

        with patch("rhythmflow.webui.lxns.urllib.request.urlopen", fake_urlopen):
            _download_file("https://assets2.lxns.net/maimai/music/1485.mp3", target)

        self.assertTrue(is_probably_mp3(target))
        self.assertEqual(calls[0], "")
        self.assertIn("__tst_status=707922460#", calls[1])
        self.assertIn("EO_Bot_Ssid=534183936", calls[1])


def _songs() -> list[dict[str, object]]:
    return [
        {
            "id": 1001,
            "title": "Stellar Parade",
            "artist": "Sample Artist",
            "version": "Festival",
            "genre": "POPS",
            "difficulties": [{"difficulty": 3, "level": "13"}],
        },
        {
            "id": 2002,
            "title": "Blue Mirage",
            "artist": "Someone",
            "version": "BUDDiES",
            "genre": "Game",
            "difficulties": [{"difficulty": 2, "level": "11+"}],
        },
        {
            "id": 3003,
            "title": "Worlds End Song",
            "artist": "Chuni Artist",
            "version": "SUN",
            "genre": "VARIETY",
            "difficulties": [{"difficulty": 5, "level": "避", "origin_id": 8888}],
        },
    ]


def _maimai_grouped_song() -> dict[str, object]:
    return {
        "id": 4004,
        "title": "Grouped Song",
        "artist": "Mai Artist",
        "version": "PRiSM",
        "genre": "Game",
        "difficulties": {
            "standard": [
                {"difficulty": 0, "level": "3"},
                {"difficulty": 3, "level": "13"},
            ],
            "dx": [
                {"difficulty": 3, "level": "13"},
                {"difficulty": 4, "level": "14"},
            ],
        },
    }


def _chunithm_ultima_song() -> dict[str, object]:
    return {
        "id": 5005,
        "title": "Ultima Song",
        "artist": "Chuni Artist",
        "version": "VERSE",
        "genre": "ORIGINAL",
        "difficulties": [{"difficulty": 4, "level": "14+"}],
    }


def _mp3_bytes() -> bytes:
    return b"ID3\x04\x00\x00\x00\x00\x00\x21" + (b"\x00" * 5000)


class _FakeResponse:
    def __init__(self, content_type: str, data: bytes) -> None:
        self.headers = {"Content-Type": content_type}
        self._data = data

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


if __name__ == "__main__":
    unittest.main()
