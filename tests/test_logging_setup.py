from __future__ import annotations

import logging
import os
import tempfile
import unittest

from rhythmflow import logging_setup


class LoggingSetupTests(unittest.TestCase):
    def test_configure_logging_writes_to_file(self) -> None:
        old_log_dir = os.environ.get("RHYTHMFLOW_LOG_DIR")
        old_sentry_dsn = os.environ.get("RHYTHMFLOW_SENTRY_DSN")
        root_logger = logging.getLogger()
        existing_handlers = set(root_logger.handlers)
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                os.environ["RHYTHMFLOW_LOG_DIR"] = temp_dir
                os.environ["RHYTHMFLOW_SENTRY_DSN"] = ""
                logging_setup._CONFIGURED = False
                logging_setup._LOG_PATH = None

                log_path = logging_setup.configure_logging()
                logging.getLogger("rhythmflow.tests").info("logging setup smoke line")
                for handler in root_logger.handlers:
                    handler.flush()

                self.assertTrue(log_path.exists())
                self.assertIn("logging setup smoke line", log_path.read_text(encoding="utf-8"))
            finally:
                for handler in list(root_logger.handlers):
                    if handler not in existing_handlers:
                        root_logger.removeHandler(handler)
                        handler.close()
                logging_setup._CONFIGURED = False
                logging_setup._LOG_PATH = None
                _restore_env("RHYTHMFLOW_LOG_DIR", old_log_dir)
                _restore_env("RHYTHMFLOW_SENTRY_DSN", old_sentry_dsn)


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
        return
    os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
