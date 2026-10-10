from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录的 .env（DeepSeek / LangSmith 密钥）
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# 抑制第三方库的噪音日志/警告（必须在导入 transformers/huggingface 之前设置）
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
