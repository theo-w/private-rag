"""中英文分词与停用词表。

单独成模块是为了让只读部署不装 qdrant-client 也能复用同一套分词：
`retrieval.lexical` 在模块顶部依赖 qdrant-client，只读服务不该被拖进那个依赖。
"""

from __future__ import annotations

import jieba


STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "有", "我", "你", "他", "她", "它",
    "吗", "呢", "啊", "吧", "就", "都", "也", "这", "那", "什么", "怎么", "为什么",
    "如何", "哪些", "哪个", "请问", "一下", "可以", "需要", "能", "会", "要", "把",
    "被", "对", "中", "上", "下", "里", "个", "但", "而", "又", "很", "更", "最",
    "还", "已经", "因为", "所以", "如果", "然后", "以及", "一个", "一种",
    "the", "a", "an", "of", "to", "in", "on", "is", "are", "was", "were", "be",
    "and", "or", "for", "with", "what", "how", "why", "which", "who", "when",
    "where", "do", "does", "did", "can", "could", "should", "would", "will",
    "this", "that", "these", "those", "it", "its", "as", "at", "by", "from",
    "we", "you", "they", "he", "she", "i",
}


def tokenize(text: str) -> list[str]:
    """中英文混合分词：去掉标点与高频停用词，保留否定词等语义词。"""
    tokens: list[str] = []
    for raw in jieba.lcut_for_search(text):
        token = raw.strip().lower()
        if not token or token in STOPWORDS:
            continue
        if not any(char.isalnum() for char in token):
            continue
        tokens.append(token)
    return tokens
