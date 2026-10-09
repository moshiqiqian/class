from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import app.config  # noqa: F401  确保 .env 已加载
import streamlit as st

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

INDEX_DIR = Path("data/chroma_db")
PARENTS_FILE = INDEX_DIR / "parents.json"

# 培养方案一级章节标题（一、二、三…）
_CHAPTER_RE = re.compile(r"^[一二三四五六七八九十]+、", re.MULTILINE)


@st.cache_resource(show_spinner=False)
def _embeddings():
    """缓存 embedding 模型，避免每次问答重复加载（加载 bge-m3 很慢）。"""
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name="BAAI/bge-m3", model_kwargs={"device": "cpu"}, encode_kwargs={"normalize_embeddings": True})


@st.cache_resource(show_spinner=False)
def _llm():
    """缓存 LLM 客户端，避免每次问答重复初始化。"""
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        temperature=0,
    )


def _split_chapters(text: str) -> list[dict]:
    """把全文按一级章节标题切分成「父块」，返回 [{title, content}]。

    例如「一、培养目标」「五、学分要求」各是一块。
    """
    # 找所有章节标题位置
    matches = list(_CHAPTER_RE.finditer(text))
    if not matches:
        return [{"title": "全文", "content": text.strip()}]
    chapters = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        title = match.group()
        content = text[start:end].strip()
        if content:
            chapters.append({"title": title, "content": content})
    return chapters


def _load_parents() -> dict[str, dict]:
    if PARENTS_FILE.exists():
        try:
            return json.loads(PARENTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_parents(parents: dict[str, dict]) -> None:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    PARENTS_FILE.write_text(json.dumps(parents, ensure_ascii=False, indent=2), encoding="utf-8")


def index_documents(pdf_bytes: bytes, filename: str) -> int:
    """建立「父文档检索」索引：
    - 按章节切父块，父块存 parents.json
    - 父块切碎片，碎片向量化入 Chroma（碎片 metadata 记录 parent_id）
    """
    from io import BytesIO

    import pdfplumber
    from langchain_community.vectorstores import Chroma

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    marker = INDEX_DIR / "source.sha256"
    if marker.exists() and marker.read_text() == digest:
        return 0

    # 1. 提取全文
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        full_text = "\n".join(page.extract_text() or "" for page in pdf.pages if page.extract_text())

    # 2. 按章节切父块
    chapters = _split_chapters(full_text)
    parents: dict[str, dict] = {}
    all_chunks: list[Document] = []

    splitter = RecursiveCharacterTextSplitter(chunk_size=300, chunk_overlap=50)
    for parent_id, chapter in enumerate(chapters):
        parents[str(parent_id)] = {"title": chapter["title"], "content": chapter["content"]}
        # 父块切碎片
        sub_docs = splitter.split_documents([Document(page_content=chapter["content"], metadata={})])
        for sub in sub_docs:
            all_chunks.append(Document(
                page_content=sub.page_content,
                metadata={"parent_id": str(parent_id), "title": chapter["title"], "source": filename},
            ))

    # 3. 存父块 + 碎片入向量库
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    _save_parents(parents)
    Chroma.from_documents(all_chunks, _embeddings(), persist_directory=str(INDEX_DIR), collection_name="curriculum")
    marker.write_text(digest)
    return len(all_chunks)


def retrieve_sections(question: str, k: int = 6) -> list[dict]:
    """父文档检索：碎片定位 → 还原父块（完整章节）。

    返回 [{title, content}]，按相关性排序、去重。
    """
    from langchain_community.vectorstores import Chroma

    if not INDEX_DIR.exists() or not PARENTS_FILE.exists():
        return []
    retriever = Chroma(persist_directory=str(INDEX_DIR), embedding_function=_embeddings(), collection_name="curriculum").as_retriever(search_kwargs={"k": k})
    hits = retriever.invoke(question)
    parents = _load_parents()
    seen: dict[str, dict] = {}
    for hit in hits:
        parent_id = hit.metadata.get("parent_id")
        if parent_id and parent_id in parents and parent_id not in seen:
            seen[parent_id] = parents[parent_id]
    return list(seen.values())


def answer(question: str, college: str, major: str, extra_context: str = "", full_text: str = "") -> tuple[str, list[dict]]:
    """生成回答。优先「父文档检索」（碎片定位 + 章节还原），
    回退「完整原文」，再回退「向量碎片」。"""
    key = os.getenv("DEEPSEEK_API_KEY", "")
    citations: list[dict] = []

    # 策略 1：父文档检索（最省 token 且上下文完整）
    sections = retrieve_sections(question)
    if sections:
        context = "\n\n".join(f"【{s['title']}】\n{s['content']}" for s in sections)
        if extra_context:
            context = f"{extra_context}\n\n{context}"
    elif full_text:
        # 策略 2：完整原文兜底
        context = full_text
        if extra_context:
            context = f"{extra_context}\n\n{context}"
    else:
        return "尚未建立 RAG 索引，无法回答。请先上传培养方案建立索引。", []

    if not key:
        return "未配置 DEEPSEEK_API_KEY，无法生成回答。", citations

    model = _llm()
    prompt = (
        f"你是「{college} · {major}」的培养方案问答助手，负责回答学生关于培养方案、选课、学分、课程安排等问题。\n\n"
        "请依据下列资料回答，做到：\n"
        "1. 数字（学分、学时、学期）必须与资料一致，不得编造。\n"
        "2. 回答完整、条理清晰：涉及多个要点时分条列出。\n"
        "3. 优先给出结论，再补充细节说明。\n"
        "4. 若资料确实不足，明确说明，不要臆测。\n\n"
        f"【资料】\n{context}\n\n"
        f"【问题】\n{question}"
    )
    return model.invoke(prompt).content, citations
