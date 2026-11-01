"""中文关键词提取。移植自 rag-knowledge 的 SearchKeywordExtractor。

Postgres 的 simple 分词切不开中文，ts_rank_cd 对中文近乎无效，
所以关键词召回靠的是这里切出来的短语 + 二元组，配合 ILIKE 匹配。
"""

import re

# 连续两个及以上的汉字算一个短语；单字太宽泛，不单独成词
CHINESE_PHRASE = re.compile(r"[一-鿿]{2,}")
LATIN_WORD = re.compile(r"[A-Za-z0-9_-]{2,}")

# 问句里的功能词，去掉后能明显减少无效匹配
IGNORED_TERMS = {
    "什么",
    "怎么",
    "如何",
    "哪些",
    "这个",
    "那个",
    "一下",
    "关于",
    "是否",
}

# 上限只用来约束 ILIKE 子句数量。定得比"典型问句能切出的词数"大，
# 否则长句后半截的二元组会被整段丢光——而症状描述的关键词往往就在后半句。
MAX_KEYWORDS = 32


def extract_keywords(question: str) -> list[str]:
    """从问句里抽关键词：整段短语 + 相邻二元组 + 英文词。

    二元组是为了兜住"转移性右下腹痛"这类整段没被知识条目原文收录、
    但局部词（右下腹、腹痛）能命中的情况。按长度降序，长的更具体。
    """
    if not question:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        if term not in seen:
            seen.add(term)
            terms.append(term)

    for match in CHINESE_PHRASE.finditer(question):
        phrase = match.group()
        add(phrase)
        for index in range(len(phrase) - 1):
            add(phrase[index : index + 2])

    for match in LATIN_WORD.finditer(question):
        add(match.group().lower())

    kept = [term for term in terms if term not in IGNORED_TERMS]
    # sorted 是稳定排序：等长的词保持原出现顺序
    kept.sort(key=len, reverse=True)
    return kept[:MAX_KEYWORDS]


def cluster_match(keyword: str, terms: list[str]) -> bool:
    """关键词是否命中同义词表。

    只认两种情况：完全相等，或词条包含在关键词里（"腹痛" ⊂ "右下腹痛"）。
    **不认关键词是词条的片段**——"不上" 是 "喘不上气" 的片段，这种匹配
    只是长句被切出来的偶然重合，不代表语义相关。
    """
    low = keyword.lower()
    return any(low == term.lower() or term.lower() in low for term in terms)


def keyword_score(
    content: str,
    keywords: list[str],
    cluster_terms: list[str] | None = None,
) -> float:
    """命中关键词数 / 关键词总数，天然封顶 1.0。

    正文按子串匹配即可；同义词表用 cluster_match 的严格规则。
    """
    if not keywords:
        return 0.0
    lowered = content.lower()
    terms = cluster_terms or []
    hits = sum(
        1
        for keyword in keywords
        if keyword.lower() in lowered or cluster_match(keyword, terms)
    )
    return hits / len(keywords)