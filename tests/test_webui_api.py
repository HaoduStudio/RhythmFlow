from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path

from rhythmflow.config import SettingsKeys
from rhythmflow.webui.api import Api
from rhythmflow.webui.events import Emitter
from rhythmflow.webui.lxns import LxnsError
from rhythmflow.webui.media_server import MediaServer


class ApiReferenceAudioTests(unittest.TestCase):
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

    def test_analyze_rejects_invalid_lxns_cache_before_ffmpeg(self) -> None:
        cache_file = Path(self._tmp.name) / "reference_audio_cache" / "maimai_1485_bad.mp3"
        cache_file.parent.mkdir(parents=True)
        cache_file.write_text("<script>challenge</script>", encoding="utf-8")
        api = Api(Emitter(), MediaServer(None))
        api.get_settings()

        result = api.analyze(["handcam.mp4"], str(cache_file), {})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "warn_reference_cache_invalid")

    def test_download_reference_audio_returns_error_payload(self) -> None:
        api = Api(Emitter(), MediaServer(None))
        api.get_settings()
        api.lxns.download_audio = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            LxnsError("asset challenge failed")
        )

        result = api.download_reference_audio("maimai", "1485", "song", False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "asset challenge failed")

    def test_osu_export_writes_chunks_to_output_dir(self) -> None:
        output_dir = Path(self._tmp.name) / "output"
        api = Api(Emitter(), MediaServer(None))
        api.save_settings({SettingsKeys.OUTPUT_DIR: str(output_dir)})

        started = api.begin_osu_export("../bad:name.mp4")
        self.assertTrue(started["ok"])
        token = str(started["token"])
        first = base64.b64encode(b"hello ").decode("ascii")
        second = base64.b64encode(b"osu").decode("ascii")

        self.assertTrue(api.append_osu_export_chunk(token, first)["ok"])
        self.assertTrue(api.append_osu_export_chunk(token, second)["ok"])
        finished = api.finish_osu_export(token)

        self.assertTrue(finished["ok"])
        path = Path(str(finished["output_path"]))
        self.assertEqual(path.parent, output_dir)
        self.assertEqual(path.read_bytes(), b"hello osu")
        self.assertNotIn("..", path.name)
        self.assertNotIn(":", path.name)

    def test_osu_export_uses_unique_output_name(self) -> None:
        output_dir = Path(self._tmp.name) / "output"
        output_dir.mkdir()
        (output_dir / "replay.mp4").write_bytes(b"existing")
        api = Api(Emitter(), MediaServer(None))
        api.save_settings({SettingsKeys.OUTPUT_DIR: str(output_dir)})

        started = api.begin_osu_export("replay.mp4")

        self.assertTrue(started["ok"])
        self.assertEqual(Path(str(started["output_path"])).name, "replay_2.mp4")


if __name__ == "__main__":
    unittest.main()
