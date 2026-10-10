"""一键清空项目数据缓存（保留本地 embedding 模型）。

用法：
    python clear_cache.py
或双击运行同目录的「清空缓存.bat」

删除：
    data/chroma_db         RAG 向量索引
    data/cache             解析缓存
    data/workspaces.json   工作区绑定
保留：
    HuggingFace 模型缓存（~/.cache/huggingface/...），避免重新下载约 2GB。
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TARGETS = [DATA / "chroma_db", DATA / "cache", DATA / "workspaces.json"]


def main() -> None:
    print(f"清空项目缓存：{ROOT}")
    for target in TARGETS:
        if target.is_dir():
            shutil.rmtree(target)
            print(f"  [删除] data/{target.name}")
        elif target.is_file():
            target.unlink()
            print(f"  [删除] data/{target.name}")
        else:
            print(f"  [跳过] data/{target.name}（不存在）")

    remaining = sorted(p.name for p in DATA.iterdir()) if DATA.exists() else []
    print(f"data/ 剩余：{remaining}")
    print("\n完成。请重启 streamlit，并在浏览器按 Ctrl+F5 强制刷新。")


if __name__ == "__main__":
    main()
