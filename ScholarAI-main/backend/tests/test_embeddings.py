"""BGE embedding 集成测试：首次跑会从 HF 下载 ~100MB（已下载则秒过）。"""
from app.rag.embeddings import embed_texts, vector_dim


def test_embed_returns_512_dim_normalized() -> None:
    assert vector_dim() == 512
    vecs = embed_texts(["你好世界", "机器学习"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 512
    # 已 normalize 的向量长度应接近 1
    import math
    norm = math.sqrt(sum(x * x for x in vecs[0]))
    assert 0.99 < norm < 1.01


def test_similar_texts_have_higher_similarity() -> None:
    vecs = embed_texts(["苹果好吃", "香蕉味甜", "今天天气晴朗"])
    import math
    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))  # 已 normalize 直接点积
    sim_fruit = cos(vecs[0], vecs[1])
    sim_fruit_weather = cos(vecs[0], vecs[2])
    # 苹果-香蕉 相似度应高于 苹果-天气
    assert sim_fruit > sim_fruit_weather
