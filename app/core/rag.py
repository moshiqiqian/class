from __future__ import annotations

import hashlib
import os
from pathlib import Path

import app.config  # noqa: F401  确保 .env 已加载

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

INDEX_DIR = Path("data/chroma_db")


def _embeddings():
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name="BAAI/bge-m3", model_kwargs={"device": "cpu"}, encode_kwargs={"normalize_embeddings": True})


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


def answer(question: str, college: str, major: str) -> tuple[str, list[dict]]:
    from langchain_community.vectorstores import Chroma
    from langchain_openai import ChatOpenAI

    if not INDEX_DIR.exists():
        return "尚未建立 RAG 索引。请先在本页建立索引。", []
    retriever = Chroma(persist_directory=str(INDEX_DIR), embedding_function=_embeddings(), collection_name="curriculum").as_retriever(search_kwargs={"k": 6})
    documents = retriever.invoke(question)
    if not documents:
        return "该问题超出当前培养方案资料范围。", []
    key = os.getenv("DEEPSEEK_API_KEY", "")
    citations = [{"page": document.metadata.get("page"), "source": document.metadata.get("source")} for document in documents]
    if not key:
        return "未配置 DEEPSEEK_API_KEY，无法生成基于检索资料的回答。", citations
    context = "\n\n".join(document.page_content for document in documents)
    model = ChatOpenAI(model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"), api_key=key, base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"), temperature=0)
    prompt = (
        f"你是「{college} · {major}」的培养方案问答助手。请依据下列资料，详细、准确地回答学生的问题。\n\n"
        "回答要求：\n"
        "1. 只依据资料回答，不得编造；数字（学分、学时、学期）必须与资料完全一致。\n"
        "2. 尽量完整：涉及多个要点时，分条列出；有数字要求时，给出具体数字。\n"
        "3. 如果资料不足以回答，请明确说明「当前资料不足以回答该问题」，不要猜测。\n"
        "4. 语气专业、简洁、友好。\n\n"
        f"【资料】\n{context}\n\n"
        f"【问题】\n{question}"
    )
    return model.invoke(prompt).content, citations
