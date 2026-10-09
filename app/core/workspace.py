from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data")
WORKSPACES_FILE = DATA_DIR / "workspaces.json"


def _load() -> list[dict]:
    if WORKSPACES_FILE.exists():
        try:
            return json.loads(WORKSPACES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save(workspaces: list[dict]) -> None:
    WORKSPACES_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKSPACES_FILE.write_text(json.dumps(workspaces, ensure_ascii=False, indent=2), encoding="utf-8")


def list_workspaces() -> list[dict]:
    return _load()


def create_workspace(name: str, pdf_name: str, cache_file: str) -> dict:
    """新建工作区。返回创建的工作区。"""
    workspaces = _load()
    # 重名时追加序号
    base = name
    counter = 1
    existing = {w["name"] for w in workspaces}
    while name in existing:
        name = f"{base} ({counter})"
        counter += 1
    workspace = {
        "name": name,
        "pdf_name": pdf_name,
        "cache_file": cache_file,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    workspaces.append(workspace)
    _save(workspaces)
    return workspace


def rename_workspace(index: int, new_name: str) -> None:
    workspaces = _load()
    if 0 <= index < len(workspaces):
        workspaces[index]["name"] = new_name
        _save(workspaces)


def delete_workspace(index: int) -> None:
    workspaces = _load()
    if 0 <= index < len(workspaces):
        workspaces.pop(index)
        _save(workspaces)


def find_cache_file(pdf_bytes: bytes) -> str:
    """根据 PDF 内容哈希，查找是否已有对应的解析缓存文件。"""
    import hashlib
    from app.core.extract_courses import CACHE_DIR
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    path = CACHE_DIR / f"catalog-{digest}.json"
    if path.exists():
        return path.name
    return ""
