# -*- coding: utf-8 -*-
"""条文切分。

原理：
  规范类文本有天然的条款边界 —— "第二百五十一条""5.2.3""一、"都是锚点。
  按锚点切出来的"条文"才是可引用的最小单元；比按字数硬切（sliding window）的答案可溯源得多。
作用：
  把一篇文档切成带「条号 + 章节路径 + 原文」的记录，让问答结果能精确回到「哪份文件第几条」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Iterator

from .clean import RE_ARTICLE, RE_CHAPTER_CN, RE_CN_HEAD, RE_NUM_HEAD

_CN_NUM = "〇零一二三四五六七八九十百千两"
RE_ARTICLE_HEAD = re.compile(rf"^\s*第\s*([{_CN_NUM}0-9]{{1,8}})\s*条\s*(.*)$")
RE_ARTICLE_INLINE = re.compile(rf"(?<=[。；\n\s])第\s*([{_CN_NUM}0-9]{{1,8}})\s*条\s*")

RE_APPENDIX = re.compile(r"^\s*附\s*录\s*([A-Za-z\u4e00-\u9fff])\s*(.*)$")

MIN_CLAUSE = 12      # 太短的碎片不成条文
MAX_CLAUSE = 1600    # 过长再二次切
FALLBACK_CHUNK = 480
FALLBACK_OVERLAP = 60


@dataclass
class Clause:
    clause_uid: str
    doc_id: str
    file_name: str
    standard_no: str
    doc_title: str
    clause_no: str          # 第251条 / 5.2.3 / 附录A
    heading: str            # 所属章节路径
    text: str               # 条文原文
    page: int
    char_len: int


def _split_sentences(text: str) -> list[str]:
    """中文句号/分号切句（用于长条文二次切分，保证不跨句断裂）。"""
    parts = re.split(r"(?<=[。；;！？?\n])", text)
    return [p for p in parts if p.strip()]


def _cluster(sents: list[str], size: int = FALLBACK_CHUNK, overlap: int = FALLBACK_OVERLAP) -> list[str]:
    """句聚类成块：按 size 累积，重叠 overlap 字防止跨块丢上下文。"""
    out, buf = [], ""
    for s in sents:
        if len(buf) + len(s) > size and buf:
            out.append(buf)
            buf = buf[-overlap:] if overlap else ""
        buf += s
    if buf.strip():
        out.append(buf)
    return out


def split_document(doc_id: str, doc_title: str, standard_no: str, file_name: str,
                   text: str, pages: list[str] | None = None) -> list[Clause]:
    """整档切条。返回 Clause 列表。"""
    lines = text.splitlines()
    clauses: list[Clause] = []
    heading_stack: list[str] = []
    cur_id, cur_head, cur_buf = "", "", []

    page_of_pos = None  # 懒构造（需要时才算页码映射）

    def flush():
        nonlocal cur_id, cur_head, cur_buf
        body = "".join(cur_buf).strip()
        if body and len(body) >= MIN_CLAUSE:
            pieces = [body] if len(body) <= MAX_CLAUSE else _cluster(_split_sentences(body))
            for i, piece in enumerate(pieces):
                suffix = f"-{i+1}" if len(pieces) > 1 else ""
                clauses.append(
                    Clause(
                        clause_uid=f"{doc_id}#{cur_id or 'p0'}{suffix}",
                        doc_id=doc_id,
                        file_name=file_name,
                        standard_no=standard_no,
                        doc_title=doc_title,
                        clause_no=cur_id or "（全文片段）",
                        heading=cur_head,
                        text=piece.strip(),
                        page=0,
                        char_len=len(piece),
                    )
                )
        cur_id, cur_head, cur_buf = "", "", []

    for ln in lines:
        s = ln.strip()
        if not s:
            continue

        m_app = RE_APPENDIX.match(s)
        m_ch = RE_CHAPTER_CN.match(s)
        m_num = RE_NUM_HEAD.match(s) if len(s) < 60 else None
        m_art = RE_ARTICLE_HEAD.match(s)

        if m_app or m_ch:
            flush()
            heading_stack = [s[:60]]
            continue
        if m_num and m_art is None:
            # 数字编号式标题，如 "5.2.3 保护装置"
            num, rest = m_num.group(1), m_num.group(2).strip()
            flush()
            heading_stack = [num + (" " + rest if rest else "")]
            continue

        # 行首即"第X条" → 新条文开始
        if m_art:
            flush()
            cur_id = f"第{m_art.group(1)}条"
            cur_head = " / ".join(heading_stack)
            cur_buf = [m_art.group(2) or ""]
            continue

        # 行内含多个"第X条"（PDF 合并 строки 后常见）
        positions = [(m.start(), m.group(1)) for m in RE_ARTICLE_INLINE.finditer(s)]
        # requires article markers actually present
        if positions and len(positions) >= 1 and not cur_buf:
            positions = [(0, positions[0][1])] + positions
            for i, (pos, num) in enumerate(positions):
                if i > 0:
                    cur_buf.append(s[:pos]) if i == 1 else None
                    flush()
                end = positions[i + 1][0] if i + 1 < len(positions) else len(s)
                cur_id = f"第{num}条"
                cur_head = " / ".join(heading_stack)
                cur_buf = [s[pos:end]]
            continue

        if cur_buf is not None:
            cur_buf.append(s)

    flush()

    # 一个锚点都没命中 → 退化为滑窗切（表格型/纯技术参数文档常见）
    if len(clauses) <= 1:
        clauses = []
        chunks = _cluster(_split_sentences(text.replace("\n", "")))
        for i, c in enumerate(chunks):
            clauses.append(
                Clause(
                    clause_uid=f"{doc_id}#c{i+1:04d}",
                    doc_id=doc_id,
                    file_name=file_name,
                    standard_no=standard_no,
                    doc_title=doc_title,
                    clause_no=f"段落{i+1}",
                    heading="",
                    text=c.strip(),
                    page=0,
                    char_len=len(c),
                )
            )
    return [c for c in clauses if len(c.text) >= MIN_CLAUSE]


def clause_to_dict(c: Clause) -> dict:
    return asdict(c)


def iter_clauses(clauses: list[Clause]) -> Iterator[Clause]:
    yield from clauses
