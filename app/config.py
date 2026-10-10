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
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")

# 若本地 HF 缓存里已有 embedding 模型，则强制离线加载：
# 直接从本地缓存读，跳过 HF Hub 的联网元数据检查（消除 “unauthenticated requests” 警告，启动更快）。
# 未缓存时保持联网，以便首次自动下载。
_HF_HOME = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
if (_HF_HOME / "hub" / "models--BAAI--bge-m3").exists():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 过滤掉第三方库的已知无害噪音日志（如缺少 torchvision 的提示）
import logging


class _NoiseFilter(logging.Filter):
    _NOISE = ("torchvision", "No module named", "unauthenticated requests")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(token in message for token in self._NOISE)


_noise_filter = _NoiseFilter()
for _logger_name in ("transformers", "sentence_transformers", "huggingface_hub", "streamlit", "watchdog", "root"):
    _logger = logging.getLogger(_logger_name)
    if not any(isinstance(f, _NoiseFilter) for f in _logger.filters):
        _logger.addFilter(_noise_filter)
for _handler in logging.getLogger().handlers:
    if not any(isinstance(f, _NoiseFilter) for f in _handler.filters):
        _handler.addFilter(_noise_filter)
