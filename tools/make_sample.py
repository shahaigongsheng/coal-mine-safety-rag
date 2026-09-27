# -*- coding: utf-8 -*-
"""生成可随仓库分发的样本语料。

原理：从完整索引里抽取"核心标准"的一小部分条文（默认 800 条），
      让克隆仓库的人不用接触原始标准文件也能一键跑通整个系统。
作用：作品可演示性 —— 面试官 clone 下来就能跑，而不是看到一个空的 data/。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 优先保留这些核心标准/规程
PREFER = [
    "架空乘人装置", "三大保护", "2022版煤矿安全规程", "煤矿安全规程2025年",
    "煤矿安全规程解读", "AQ 1029", "AQ 1119", "人员定位", "GB 51145",
    "重大事故隐患", "保护接地", "检漏", "智能化",
]


def score(row: dict) -> int:
    fn = row["file_name"]
    s = 0
    for i, kw in enumerate(PREFER):
        if kw in fn:
            s += len(PREFER) - i
    # 有明确条号的优先（可溯源演示效果最好）；"全文片段"降权
    if row["clause_no"] and not row["clause_no"].startswith("段落"):
        s += 10
    if row["clause_no"].startswith("（全文片段）") or row["clause_no"].startswith("段落"):
        s -= 50
    if 60 <= row["char_len"] <= 600:
        s += 3
    return s


def main(n: int = 800):
    src = ROOT / "data" / "clauses.jsonl"
    dst = ROOT / "data" / "sample_clauses.jsonl"
    rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    # 分文件成组 → 轮转取样，保证样本覆盖多份标准而不是被一份刷屏
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["file_name"], []).append(r)
    for g in groups.values():
        g.sort(key=score, reverse=True)
    # 只保留命中核心关键词的文档，且每份最多取 25 条
    KEY_DOCS = [k for k in groups if any(kw in k for kw in PREFER)]
    for k in KEY_DOCS:
        groups[k] = groups[k][:25]
    order = sorted(KEY_DOCS, key=lambda k: -score(groups[k][0]))
    picked, i = [], 0
    while len(picked) < n:
        added = False
        for k in order:
            if i < len(groups[k]) and len(picked) < n:
                picked.append(groups[k][i])
                added = True
        if not added:
            break
        i += 1
    with dst.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    files = {r["file_name"] for r in picked}
    print(f"样本：{len(picked)} 条 / {len(files)} 份文档 → {dst}（{dst.stat().st_size/1024:.0f} KB）")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 800)
