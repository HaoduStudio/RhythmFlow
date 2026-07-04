from __future__ import annotations

SR = 22050
N_FFT = 4096
HOP = 1024
FMIN = 55.0
FMAX = 5000.0

ORG_NAME = "HaoduStudio"
APP_NAME = "RhythmFlow"
APP_AUTHOR = "HaoduStudio"
REPOSITORY_URL = "https://github.com/HaoduStudio/RhythmFlow"

DEFAULT_ORIGINAL_VOLUME = 15
DEFAULT_REFERENCE_VOLUME = 100
DEFAULT_OUTPUT_PATTERN = "{name}_aligned.mp4"
DEFAULT_THEME = "dark_teal.xml"
DEFAULT_LANGUAGE = "zh"
DEFAULT_FONT_FAMILY = "Microsoft YaHei UI"
SENTRY_DSN = "https://3b864f170d394bb7866196376946f745@o4509333304573952.ingest.us.sentry.io/4511674939736064"


class SettingsKeys:
    THEME = "theme"
    LANGUAGE = "language"
    OUTPUT_DIR = "output_dir"
    OUTPUT_PATTERN = "output_pattern"
    ORIGINAL_VOLUME = "original_volume"
    REFERENCE_VOLUME = "reference_volume"
    CUT_MODE = "cut_mode"
