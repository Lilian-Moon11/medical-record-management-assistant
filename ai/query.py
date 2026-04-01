# Copyright (C) 2026 Lilian-Moon11
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or any later version.

# -----------------------------------------------------------------------------
# PURPOSE:
# Citation query engine over the document_chunks table.
#
# Wraps chunks in a lightweight in-memory llama-index VectorStoreIndex,
# using CitationQueryEngine so every response includes source citations.
#
# Public API:
#   query_documents(conn, patient_id, question, llm=None)
#       -> {"response": str, "citations": [{"doc_id", "page_number",
#                                           "source_file_name"}]}
# -----------------------------------------------------------------------------

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _load_chunks(conn, patient_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, doc_id, page_number, source_file_name, chunk_text
        FROM document_chunks
        WHERE patient_id = ?
        ORDER BY doc_id, chunk_index
        """,
        (patient_id,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def query_documents(
    conn,
    patient_id: int,
    question: str,
    llm=None,
) -> dict:
    """
    Query the patient's indexed document chunks and return a cited response.

    Returns
    -------
    dict with keys:
        response   : str  — the LLM answer
        citations  : list of {"doc_id", "page_number", "source_file_name"}
    """
    from llama_index.core import VectorStoreIndex, Document, Settings
    from llama_index.core.query_engine import CitationQueryEngine

    if llm is None:
        from ai.backend import get_llm
        llm = get_llm()

    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    Settings.llm = llm
    Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

    chunks = _load_chunks(conn, patient_id)
    if not chunks:
        return {
            "response": (
                "No documents have been indexed yet for this patient. "
                "Please allow the indexing process to finish, then try again."
            ),
            "citations": [],
        }

    # Build llama-index Documents from chunks, preserving metadata
    documents = [
        Document(
            text=c["chunk_text"],
            metadata={
                "doc_id": c["doc_id"],
                "page_number": c["page_number"],
                "source_file_name": c["source_file_name"] or "unknown",
            },
        )
        for c in chunks
    ]

    index = VectorStoreIndex.from_documents(documents)
    engine = CitationQueryEngine.from_args(index, similarity_top_k=5)

    result = engine.query(question)

    # Collect unique citation metadata from source nodes
    seen = set()
    citations = []
    for node in getattr(result, "source_nodes", []):
        meta = node.metadata or {}
        key = (meta.get("doc_id"), meta.get("page_number"))
        if key not in seen:
            seen.add(key)
            citations.append({
                "doc_id": meta.get("doc_id"),
                "page_number": meta.get("page_number"),
                "source_file_name": meta.get("source_file_name", "unknown"),
            })

    return {
        "response": str(result),
        "citations": citations,
    }


def query_documents_stream(
    conn,
    patient_id: int,
    question: str,
    llm=None,
):
    """
    Query the patient's indexed document chunks and YIELD cited response chunks.
    Yields dicts representing the stream state.
    """
    from llama_index.core import VectorStoreIndex, Document, Settings
    from llama_index.core.query_engine import CitationQueryEngine

    if llm is None:
        from ai.backend import get_llm
        llm = get_llm()

    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    Settings.llm = llm
    Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-small-en-v1.5")

    chunks = _load_chunks(conn, patient_id)
    if not chunks:
        yield {
            "type": "chunk", 
            "text": "No documents have been indexed yet for this patient. Please allow the indexing process to finish, then try again."
        }
        yield {"type": "citations", "citations": []}
        return

    documents = [
        Document(
            text=c["chunk_text"],
            metadata={
                "doc_id": c["doc_id"],
                "page_number": c["page_number"],
                "source_file_name": c["source_file_name"] or "unknown",
            },
        )
        for c in chunks
    ]

    index = VectorStoreIndex.from_documents(documents)
    engine = CitationQueryEngine.from_args(index, similarity_top_k=5, streaming=True)

    result = engine.query(question)

    for text_chunk in result.response_gen:
        yield {"type": "chunk", "text": text_chunk}

    seen = set()
    citations = []
    for node in getattr(result, "source_nodes", []):
        meta = node.metadata or {}
        key = (meta.get("doc_id"), meta.get("page_number"))
        if key not in seen:
            seen.add(key)
            citations.append({
                "doc_id": meta.get("doc_id"),
                "page_number": meta.get("page_number"),
                "source_file_name": meta.get("source_file_name", "unknown"),
            })

    yield {"type": "citations", "citations": citations}
