"""重建知识库索引：python -m backend.app.knowledge

放在包的 __main__ 里而不是 indexer.py 里：包的 __init__ 已经 import 过
indexer，用 -m 直接跑子模块会让它被加载两次。
"""

from backend.app.knowledge.indexer import build_index

if __name__ == "__main__":
    print(build_index())