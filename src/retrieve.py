# -*- coding: utf-8 -*-
"""检索 + 回答引擎。

原理（三路合一）：
  1) BM25  —— 稀疏检索，对"条文编号/专有名词"这类字面精确匹配极强。
             例：问「第二百五十一条」，BM25 一击必中，向量检索反而会漂。
  2) 向量  —— 稠密检索，对"口语化提问"补位。
             例：问「井下能不能存汽油」，原文写的是「井下严禁存放煤油」，字面不重合。
  3) 融合  —— RRF（倒数排名融合），两路都不丢；再用"标准号命中"做小幅度加权。

作用：在没有 API key、纯离线的情况下系统依然可用（provider=none 时只用 BM25）。
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rank_bm25 import BM25Okapi  # noqa: E402

import jieba  # noqa: E402

_DICT = ROOT / "data" / "dict_coal.txt"
if _DICT.exists():
    for line in _DICT.read_text(encoding="utf-8").splitlines():
        w = line.strip()
        if w:
            jieba.add_word(w)

import yaml  # noqa: E402


@dataclass
class Hit:
    clause_uid: str
    score: float
    file_name: str
    standard_no: str
    doc_title: str
    clause_no: str
    heading: str
    text: str
    fuse_detail: dict = field(default_factory=dict)


def tokenize(s: str) -> list[str]:
    """中文分词 + 标准号/编号增强。

    作用：让 "AQ1038" 这类编号不被切成碎片，保证检索时能整词命中。
    """
    s = s.lower()
    toks: list[str] = []
    for m in _TOKEN_ENHANCE.finditer(s):
        toks.append(m.group(0).replace(" ", "").replace("_", ""))
    toks += [t for t in jieba.lcut(s) if t.strip() and len(t) > 1]
    return toks


import re  # noqa: E402

_TOKEN_ENHANCE = re.compile(
    r"\b(?:gb|aq|mt|nb|ka|jt|dl|jjf)\s*[/_]?\s*t?\s*[\d.]{3,8}", re.I
)
RE_STD_QUERY = re.compile(r"(gb|aq|mt|nb|ka)\s*[/_]?\s*t?\s*([\d.]{3,8})", re.I)
RE_CLAUSE_QUERY = re.compile(r"第\s*([〇零一二三四五六七八九十百千两0-9]{1,8})\s*条")

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9}


def cn2int(s: str) -> int:
    """中文数字 → 整数。支持「二百五十一」「二千零二十五」「251」。

    作用：用户问「第二百五十一条」时能直接和索引里的条号对齐，不被分词打散。
    """
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch == "十":
            section += (num if num else 1) * 10
            num = 0
        elif ch == "百":
            section += (num if num else 1) * 100
            num = 0
        elif ch == "千":
            section += (num if num else 1) * 1000
            num = 0
        else:
            return -1
    return total + section + num


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


class Engine:
    """问答引擎：加载条文语料与索引，提供检索与（可选）生成式回答。"""

    def __init__(self, cfg: dict | None = None, lazy: bool = False):
        self.cfg = cfg or load_cfg()
        self.data_dir = ROOT / self.cfg["project"]["data_dir"]
        self.index_dir = ROOT / self.cfg["project"]["index_dir"]
        self.clauses: list[dict] = []
        self.bm25: BM25Okapi | None = None
        self.tokens: list[list[str]] = []
        self.embed_fn: Callable[[list[str]], list[list[float]]] | None = None
        self.emb_matrix = None
        if not lazy:
            self.load()

    # ---------------------------------------------------------------- 加载
    def load(self):
        t0 = time.time()
        clauses_file = self.data_dir / self.cfg["project"].get("clauses_file", "clauses.jsonl")
        self.clauses = [
            json.loads(l)
            for l in clauses_file.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        idx_file = self.index_dir / "bm25.pkl"
        if idx_file.exists():
            blob = pickle.loads(idx_file.read_bytes())
            if blob.get("n") == len(self.clauses):
                self.bm25, self.tokens = blob["bm25"], blob["tokens"]
        if self.bm25 is None:
            self._build_bm25()
        self._init_embedding()
        self.load_seconds = round(time.time() - t0, 2)

    def _build_bm25(self):
        self.tokens = [tokenize(c["text"] + " " + c["clause_no"] + " " + c["heading"]) for c in self.clauses]
        self.bm25 = BM25Okapi(self.tokens)
        if not self.index_dir.exists():
            self.index_dir.mkdir(parents=True, exist_ok=True)
        (self.index_dir / "bm25.pkl").write_bytes(
            pickle.dumps({"n": len(self.clauses), "bm25": self.bm25, "tokens": self.tokens})
        )

    def _init_embedding(self):
        """按 config 决定是否启用向量检索。none → 只用 BM25，离线可跑。"""
        prov = self.cfg["embedding"]["provider"]
        if prov == "none":
            return
        if prov == "api":
            try:
                from openai import OpenAI
            except ImportError:
                print("[warn] 未安装 openai，退回纯 BM25")
                return
            key = os.getenv(self.cfg["embedding"]["api_key_env"], "")
            if not key:
                print("[warn] 未设置 OPENAI_API_KEY，退回纯 BM25")
                return
            cli = OpenAI(base_url=self.cfg["embedding"]["api_base"], api_key=key)
            model = self.cfg["embedding"]["model"]

            def emb(texts: list[str]) -> list[list[float]]:
                r = cli.embeddings.create(model=model, input=texts)
                return [d.embedding for d in r.data]

            self.embed_fn = emb
        elif prov == "local":
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                print("[warn] 未安装 sentence-transformers，退回纯 BM25")
                return
            m = SentenceTransformer(self.cfg["embedding"]["model"])
            self.embed_fn = lambda ts: m.encode(ts, normalize_embeddings=True).tolist()

        if self.embed_fn is not None:
            vec_file = self.index_dir / "vec.pkl"
            if vec_file.exists():
                blob = pickle.loads(vec_file.read_bytes())
                if blob.get("n") == len(self.clauses):
                    self.emb_matrix = blob["mat"]
            if self.emb_matrix is None:
                import numpy as np

                texts = [c["text"][:800] for c in self.clauses]
                self.emb_matrix = np.array(self.embed_fn(texts), dtype="float32")
                self.index_dir.mkdir(parents=True, exist_ok=True)
                vec_file.write_bytes(pickle.dumps({"n": len(self.clauses), "mat": self.emb_matrix}))

    # ---------------------------------------------------------------- 检索
    def _std_boost(self, query: str) -> dict[str, float]:
        """标准号命中加权：问句里出现 AQ1038 时，该标准条文优先。

        作用：解决"AQ1038 对制动距离怎么要求的"这类跨条文提问的指向性。
        """
        m = RE_STD_QUERY.search(query)
        if not m:
            return {}
        key = m.group(1).upper() + m.group(2).replace(" ", "")
        boost = {}
        for i, c in enumerate(self.clauses):
            sn = (c.get("standard_no") or "").replace(" ", "").replace("_", "").upper()
            if sn.startswith(key) or key in c["file_name"].upper().replace(" ", "").replace("_", ""):
                boost[i] = 0.35
        return boost

    def search(self, query: str, topk: int = 8, filters: dict | None = None) -> list[Hit]:
        filters = filters or {}
        candidates: list[int] = list(range(len(self.clauses)))
        if filters.get("file_name"):
            candidates = [i for i in candidates if self.clauses[i]["file_name"] == filters["file_name"]]
        if filters.get("standard_no"):
            candidates = [i for i in candidates if self.clauses[i]["standard_no"] == filters["standard_no"]]

        r = self.cfg["retrieval"]
        q_tokens = tokenize(query)
        bm25_scores = self.bm25.get_scores(q_tokens) if self.bm25 else None

        max_bm = float(max(bm25_scores[i] for i in candidates)) if bm25_scores is not None else 0.0

        # --- BM25 路
        bm_rank: dict[int, int] = {}
        if bm25_scores is not None:
            order = sorted(candidates, key=lambda i: -bm25_scores[i])[: r["bm25_topk"]]
            bm_rank = {idx: rank + 1 for rank, idx in enumerate(order)}

        # --- 向量路
        vec_rank: dict[int, int] = {}
        if self.emb_matrix is not None and self.embed_fn is not None:
            import numpy as np

            qv = np.array(self.embed_fn([query])[0], dtype="float32")
            sims = (self.emb_matrix @ qv) / (
                np.linalg.norm(self.emb_matrix, axis=1) * np.linalg.norm(qv) + 1e-9
            )
            order_v = sorted(candidates, key=lambda i: -sims[i])[: r["vector_topk"]]
            vec_rank = {idx: rank + 1 for rank, idx in enumerate(order_v)}

        # --- 条款精确命中路：问句含「第X条」时，直接锁定该条号，权重大幅提高
        clause_rank: dict[int, int] = {}
        m_clause = RE_CLAUSE_QUERY.search(query)
        if m_clause:
            want = cn2int(m_clause.group(1))
            got = []
            if want > 0:
                # 注意：这里必须扫全库候选，不能只在 BM25 Top-N 里找。
                # 中文数字条号常被分词切碎，BM25 前 N 名往往根本不含该条。
                for i in candidates:
                    cn = self.clauses[i].get("clause_no", "")
                    mm = re.match(r"第([〇零一二三四五六七八九十百千两0-9]{1,8})条", cn)
                    if mm and cn2int(mm.group(1)) == want:
                        got.append(i)
            for rank, i in enumerate(got):
                clause_rank[i] = rank + 1

        # --- RRF 融合
        k = r["rrf_k"]
        boost = self._std_boost(query)
        fused: dict[int, float] = {}
        for idx in set(bm_rank) | set(vec_rank) | set(clause_rank):
            s = 0.0
            if idx in bm_rank:
                s += 1.0 / (k + bm_rank[idx])
                # 把 BM25 原始分归一后叠加，让"相关度"而非只有"名次"影响最终分
                if max_bm > 0:
                    s += 0.35 * (bm25_scores[idx] / max_bm)
            if idx in vec_rank:
                s += 1.0 / (k + vec_rank[idx])
            if idx in clause_rank:
                s += 3.0 / (k + clause_rank[idx])  # 条款精确命中：压倒性优先
            s *= 1.0 + boost.get(idx, 0.0)
            fused[idx] = s

        ranked = sorted(fused, key=lambda i: -fused[i])[: topk * 3]
        # 多样性：同一文件最多占一半名额，避免答案被同一本解读刷屏
        per_file, final = {}, []
        for i in ranked:
            n = per_file.get(self.clauses[i]["file_name"], 0)
            cap = topk if filters.get("file_name") else max(2, topk // 2)
            if n >= cap:
                continue
            per_file[self.clauses[i]["file_name"]] = n + 1
            final.append(i)
            if len(final) >= topk:
                break
        ranked = final
        hits = []
        for i in ranked:
            c = self.clauses[i]
            hits.append(
                Hit(
                    clause_uid=c["clause_uid"],
                    score=round(fused[i], 5),
                    file_name=c["file_name"],
                    standard_no=c.get("standard_no", ""),
                    doc_title=c.get("doc_title", ""),
                    clause_no=c.get("clause_no", ""),
                    heading=c.get("heading", ""),
                    text=c["text"],
                    fuse_detail={"bm25": bm_rank.get(i), "vec": vec_rank.get(i),
                                 "条号命中": clause_rank.get(i), "加权": boost.get(i, 0)},
                )
            )
        return hits

    # ---------------------------------------------------------------- 生成
    def answer(self, query: str, topk: int = 6) -> dict:
        hits = self.search(query, topk=topk)
        provider = self.cfg["llm"]["provider"]
        if provider == "none":
            return {"mode": "retrieval", "hits": hits, "answer": ""}
        key = os.getenv(self.cfg["llm"]["api_key_env"], "")
        if not key:
            return {"mode": "retrieval", "hits": hits, "answer": "", "warn": "未设置 OPENAI_API_KEY"}
        try:
            from openai import OpenAI
        except ImportError:
            return {"mode": "retrieval", "hits": hits, "answer": "", "warn": "未安装 openai"}

        cli = OpenAI(base_url=self.cfg["llm"]["api_base"], api_key=key)
        blocks = []
        for i, h in enumerate(hits, 1):
            src = f"{h.file_name}" + (f"（{h.standard_no}）" if h.standard_no else "") + f" {h.clause_no}"
            blocks.append(f"[{i}] 来源：{src}\n{h.text}")
        prompt = (
            "你是煤矿安全标准问答助手。只能依据下面给出的条文原文回答，不得编造。\n"
            "回答时每个要点后面用 [n] 标注引用编号；若条文不足以回答，直接说明未检索到依据。\n\n"
            "条文：\n" + "\n\n".join(blocks) + f"\n\n问题：{query}\n回答："
        )
        try:
            r = cli.chat.completions.create(
                model=self.cfg["llm"]["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=self.cfg["llm"]["temperature"],
                max_tokens=self.cfg["llm"]["max_tokens"],
            )
            ans = r.choices[0].message.content
        except Exception as e:
            return {"mode": "retrieval", "hits": hits, "answer": "", "warn": f"LLM 调用失败：{e}"}
        return {"mode": "rag", "hits": hits, "answer": ans}

    # ---------------------------------------------------------------- 统计
    def stats(self) -> dict:
        files = {c["file_name"] for c in self.clauses}
        stds = {c["standard_no"] for c in self.clauses if c.get("standard_no")}
        return {
            "clauses": len(self.clauses),
            "files": len(files),
            "standards": len(stds),
            "chars": sum(c["char_len"] for c in self.clauses),
        }
