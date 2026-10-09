from __future__ import annotations

import hashlib
import os
from pathlib import Path

import app.config  # noqa: F401  确保 .env 已加载
import streamlit as st

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

INDEX_DIR = Path("data/chroma_db")


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


def index_documents(pdf_bytes: bytes, filename: str) -> int:
    from io import BytesIO

    import pdfplumber
    from langchain_community.vectorstores import Chroma

    digest = hashlib.sha256(pdf_bytes).hexdigest()
    marker = INDEX_DIR / "source.sha256"
    if marker.exists() and marker.read_text() == digest:
        return 0
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        documents = [Document(page_content=page.extract_text() or "", metadata={"source": filename, "page": number + 1}) for number, page in enumerate(pdf.pages) if page.extract_text()]
    chunks = RecursiveCharacterTextSplitter(chunk_size=900, chunk_overlap=150).split_documents(documents)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    Chroma.from_documents(chunks, _embeddings(), persist_directory=str(INDEX_DIR), collection_name="curriculum")
    marker.write_text(digest)
    return len(chunks)


def answer(question: str, college: str, major: str, extra_context: str = "", full_text: str = "") -> tuple[str, list[dict]]:
    """生成回答。优先使用「完整专业原文」full_text（质量最高），
    否则回退到向量检索（retriever）。"""
    from langchain_community.vectorstores import Chroma

    key = os.getenv("DEEPSEEK_API_KEY", "")
    citations: list[dict] = []
    documents = []

    # 策略 1：有完整原文时，直接用它（上下文最全，回答质量最好）
    if full_text:
        context = full_text
        if extra_context:
            context = f"{extra_context}\n\n{context}"
    else:
        # 策略 2：向量检索
        if not INDEX_DIR.exists():
            return "尚未建立 RAG 索引。请先在本页建立索引。", []
        retriever = Chroma(persist_directory=str(INDEX_DIR), embedding_function=_embeddings(), collection_name="curriculum").as_retriever(search_kwargs={"k": 8})
        documents = retriever.invoke(question)
        if not documents and not extra_context:
            return "该问题超出当前培养方案资料范围。", []
        citations = [{"page": document.metadata.get("page"), "source": document.metadata.get("source")} for document in documents]
        context = "\n\n".join(document.page_content for document in documents)
        if extra_context:
            context = f"{extra_context}\n\n{context}"

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
