# -*- coding: utf-8 -*-
"""检索质量自测。

用法：  python -m src.eval
输出：  每个问题的 Top1/3/5 命中情况 + 汇总准确率 + data/eval_report.md
原理：  只用检索层，不引入大模型打分 —— 这样指标可复现、可回归。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.retrieve import Engine  # noqa: E402

TOPK = 8


def hit(clauses, expect_any, expect_clause=None) -> bool:
    if expect_clause:
        for c in clauses:
            if c["clause_no"] == expect_clause:
                return True
    if expect_any:
        blob = "\n".join(c["text"] + c["clause_no"] for c in clauses)
        for kw in expect_any:
            if kw in blob:
                return True
    return False


def main(topn=(1, 3, 5)):
    qs = yaml.safe_load((ROOT / "data" / "eval_questions.yaml").read_text(encoding="utf-8"))
    eng = Engine()
    print(f"索引：{eng.stats()}\n开始自测 {len(qs)} 题 ...\n")

    results, t0 = [], time.time()
    agg = {n: 0 for n in topn}
    for item in qs:
        q = item["q"]
        hits = eng.search(q, topk=TOPK)
        recs = [{
            "file_name": h.file_name, "standard_no": h.standard_no,
            "clause_no": h.clause_no, "text": h.text, "score": h.score,
        } for h in hits]
        line = {"q": q, "domain": item.get("domain", "")}
        for n in topn:
            ok = hit(recs[:n], item.get("expect_any"), item.get("expect_clause"))
            line[f"top{n}"] = ok
            agg[n] += int(ok)
        line["best"] = f"{recs[0]['file_name'][:30]} | {recs[0]['clause_no']}" if recs else ""
        results.append(line)
        flag = "".join("✔" if line[f"top{n}"] else "✘" for n in topn)
        print(f"  {flag}  {q[:34]:36s} {line['best'][:50]}")

    total = len(qs)
    print(f"\n=== 汇总（{total} 题，耗时 {time.time()-t0:.0f}s）===")
    for n in topn:
        print(f"  Top-{n} 命中率：{agg[n]}/{total} = {agg[n]/total:.1%}")

    # 报告
    md = ["# 检索质量自测报告", "", f"- 语料：{eng.stats()['files']} 份文档 / {eng.stats()['clauses']:,} 条条文",
          f"- 模式：{'BM25+向量' if eng.emb_matrix is not None else 'BM25 单路'}",
          "", "| 命中 | 问题 | 领域 | Top1 出处 |", "|---|---|---|---|"]
    for r in results:
        flag = "".join("✔" if r[f"top{n}"] else "✘" for n in topn)
        md.append(f"| {flag} | {r['q']} | {r['domain']} | {r['best']} |")
    md.append("")
    md.append("> 列序为 Top1 / Top3 / Top5")
    (ROOT / "data" / "eval_report.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n报告：{ROOT/'data'/'eval_report.md'}")


if __name__ == "__main__":
    main()
