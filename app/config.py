from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录的 .env（DeepSeek / LangSmith 密钥）
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
