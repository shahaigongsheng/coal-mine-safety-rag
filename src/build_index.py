# -*- coding: utf-8 -*-
"""构建条文索引：data/extracted/*.json → data/clauses.jsonl（核心产物）。

链路：抽取缓存 → 按条款切分 → 补页码 → 去重 → 落盘 JSONL
口径：clauses.jsonl 是本项目唯一事实源，检索层只读它。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.split import split_document, clause_to_dict  # noqa: E402


def _page_of_char(pages_len: list[int]) -> list[int]:
    """字符位移 → 页码映射表。

    原理：先算每页累积字符区间；条文在正文里的字符位置落在哪个区间，就属于哪一页。
    作用：出处能显示"第几页"，方便回原文核对 —— 规范和答疑场景下，这是工程师的实际查阅习惯。
    """
    cum, mapping = 0, []
    for i, ln in enumerate(pages_len):
        cum += ln + 1
        mapping.append(cum)
    return mapping


def locate_page(pos: int, mapping: list[int]) -> int:
    import bisect

    return bisect.bisect_left(mapping, pos) + 1


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    data_dir = ROOT / cfg["project"]["data_dir"]
    cache_dir = data_dir / "extracted"
    if not cache_dir.exists():
        print("[err] 没有抽取缓存，先跑 python -m src.extract --no-ocr")
        return

    files = sorted(cache_dir.glob("*.json"))
    print(f"读取 {len(files)} 份抽取缓存 ...")
    t0 = time.time()

    all_clauses: list[dict] = []
    docs: list[dict] = []
    skipped: Counter = Counter()

    for fp in files:
        r = json.loads(fp.read_text(encoding="utf-8"))
        if not r.get("text") or r.get("quality") not in ("ok", "ocr-ok", "ocr"):
            skipped[r.get("quality", "?")] += 1
            continue
        mapping = _page_of_char(r.get("pages_len") or [])
        clauses = split_document(
            doc_id=r["doc_id"],
            doc_title=r.get("title") or r["file_name"],
            standard_no=r.get("standard_no", ""),
            file_name=r["file_name"],
            text=r["text"],
        )
        # 页码回填
        pos = 0
        for c in clauses:
            if mapping:
                c.page = locate_page(pos, mapping)
            pos += len(c.text) + 1
        all_clauses.extend(clause_to_dict(c) for c in clauses)
        docs.append({
            "doc_id": r["doc_id"], "file_name": r["file_name"], "title": r.get("title", ""),
            "standard_no": r.get("standard_no", ""), "quality": r.get("quality", ""),
            "rel_path": r.get("rel_path", ""), "chars": len(r["text"]), "clauses": len(clauses),
        })

    # 去重：同一标准号和条号的重复条目（多副本文件常见）
    seen, uniq = set(), []
    for c in all_clauses:
        key = (c["standard_no"], c["clause_no"], c["text"][:60])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    data_dir.mkdir(parents=True, exist_ok=True)
    clauses_file = data_dir / cfg["project"].get("clauses_file", "clauses.jsonl")
    with clauses_file.open("w", encoding="utf-8") as f:
        for c in uniq:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    (data_dir / "docs.json").write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 索引构建完成（{time.time()-t0:.1f}s）===")
    print(f"  条文总数 : {len(uniq)}（去重前 {len(all_clauses)}）")
    print(f"  文档数   : {len(docs)}")
    print(f"  总字数   : {sum(c['char_len'] for c in uniq):,}")
    print(f"  跳过     : {dict(skipped)}")
    print(f"  产出     : {data_dir/'clauses.jsonl'}")

    top = Counter(d["file_name"] for d in docs).most_common(10)
    print("\n  条文最多的文档 TOP10：")
    for name, n in top:
        dn = next(d for d in docs if d["file_name"] == name)
        print(f"    {dn['clauses']:5d} 条  {name[:56]}")


if __name__ == "__main__":
    main()
