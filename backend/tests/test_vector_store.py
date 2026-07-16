"""向量库 smoke test：用假 embedding 函数验证 add/query/delete 流程。"""
from __future__ import annotations

import uuid

from app.rag import vector_store as vs


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """确定性假向量：长度 8，sum(words) 决定数值。"""
    out = []
    for t in texts:
        s = sum(ord(c) for c in t) % 1000 / 1000.0
        out.append([s, 1 - s, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    return out


def test_add_query_delete_cycle() -> None:
    kb_id = f"test-kb-{uuid.uuid4().hex[:8]}"
    coll = vs.get_or_create_collection(kb_id)
    coll.add(
        ids=["d1", "d2", "d3"],
        embeddings=_fake_embed(["苹果好吃", "香蕉味甜", "今天天气"]),
        documents=["苹果好吃", "香蕉味甜", "今天天气"],
        metadatas=[{"src": "a"}, {"src": "a"}, {"src": "b"}],
    )
    assert vs.collection_count(kb_id) == 3

    # query 用与"苹果好吃"相同的字符 sum，得到相同向量 → 期望 d1 排第一
    q_vec = _fake_embed(["苹果好吃"])[0]
    res = coll.query(query_embeddings=[q_vec], n_results=2)
    assert res["ids"][0][0] == "d1"
    assert "苹果" in res["documents"][0][0]

    vs.delete_collection(kb_id)
    assert vs.collection_count(kb_id) == 0
