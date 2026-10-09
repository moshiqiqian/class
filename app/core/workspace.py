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


def get_workspace(name: str) -> dict | None:
    return next((w for w in _load() if w["name"] == name), None)


def name_exists(name: str) -> bool:
    return any(w["name"] == name for w in _load())


def create_workspace(name: str) -> dict:
    """创建「空工作区」（仅名称，尚未绑定方案）。返回创建的工作区。

    重名时自动追加序号。
    """
    workspaces = _load()
    base = name
    counter = 1
    existing = {w["name"] for w in workspaces}
    while name in existing:
        name = f"{base} ({counter})"
        counter += 1
    workspace = {
        "name": name,
        "pdf_name": None,      # 未上传方案
        "cache_file": None,    # 未绑定解析缓存
        "profile": None,       # 学生画像（待填写）
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    workspaces.append(workspace)
    _save(workspaces)
    return workspace


def bind_pdf(workspace_name: str, pdf_name: str, cache_file: str) -> None:
    """把解析好的方案（PDF + 缓存）绑定到工作区。"""
    workspaces = _load()
    for w in workspaces:
        if w["name"] == workspace_name:
            w["pdf_name"] = pdf_name
            w["cache_file"] = cache_file
            break
    _save(workspaces)


def save_profile(workspace_name: str, profile: dict) -> None:
    """把学生画像保存到指定工作区（工作区与学生绑定）。"""
    workspaces = _load()
    for w in workspaces:
        if w["name"] == workspace_name:
            w["profile"] = profile
            break
    _save(workspaces)


def delete_workspace_by_name(name: str) -> None:
    """按名称删除工作区。"""
    workspaces = _load()
    _save([w for w in workspaces if w["name"] != name])


def find_cache_file(pdf_bytes: bytes) -> str:
    """根据 PDF 内容哈希，查找是否已有对应的解析缓存文件。"""
    import hashlib
    from app.core.extract_courses import CACHE_DIR
    digest = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    path = CACHE_DIR / f"catalog-{digest}.json"
    if path.exists():
        return path.name
    return ""
