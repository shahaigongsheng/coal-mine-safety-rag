# -*- coding: utf-8 -*-
"""文本清洗与质量判定。

原理：
  1) 工业 PDF 有两类"坏的"：扫描件（无文本层）、CID 字体未嵌入 ToUnicode（抽出来是乱码）。
     二者都不能直接入库，必须 OCR。
  2) 抽取出来的"好"文本也不干净：页眉页脚、断行、全角/半角混排、中文夹空格。
作用：
  统一归一化为可检索的干净正文，并给出 `quality` 标记供上层决定走直读还是 OCR。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Tuple

# ---------------------------------------------------------------- 字符集工具

CJK = r"\u4e00-\u9fff"
CJK_PUNCT = r"\u3000-\u303f\uff00-\uffef"
_CN_NUM = "〇零一二三四五六七八九十百千两"

# 「第X条」/「第X章」样式，覆盖 2025 版《煤矿安全规程》与多数行业标准
RE_ARTICLE = re.compile(rf"第\s*[{_CN_NUM}0-9]{{1,8}}\s*条")
RE_CHAPTER_CN = re.compile(rf"^第\s*[{_CN_NUM}0-9]{{1,4}}\s*[章编篇節节]\s*(.*)$")
# 数字编号式：5.2.3 / 6.1 / A.2
RE_NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+){1,4})\s*(.*)$")
# 中文序号条款：一、总则 / （三） xxx
RE_CN_HEAD = re.compile(rf"^\s*[（(]?[{_CN_NUM}]{{1,3}}[)）、．.]\s*(.*)$")

_FULL2HALF = {ord("０") + i: ord("0") + i for i in range(10)}
_FULL2HALF.update({ord("Ａ") + i: ord("A") + i for i in range(26)})
_FULL2HALF.update({ord("ａ") + i: ord("a") + i for i in range(26)})
_FULL2HALF.update({ord("．"): ord("."), ord("，"): ord(","), ord("："): ord(":")})


def _cjk_ratio(s: str) -> float:
    if not s:
        return 0.0
    n = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    return n / len(s)


def _weird_ratio(s: str) -> float:
    """非中、非常见 ASCII 的"怪字符"占比 —— CID 乱码的典型特征。"""
    if not s:
        return 1.0
    weird = 0
    for ch in s:
        o = ord(ch)
        if "\u4e00" <= ch <= "\u9fff":
            continue
        if o < 128:
            continue
        if 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:  # 中文标点
            continue
        weird += 1
    return weird / len(s)


def judge_quality(text: str, pages: int) -> Tuple[str, str]:
    """判定抽取文本质量。

    返回 (quality, reason)，quality ∈ {ok, garbled, scanned}
    - ok      ：可直接入库
    - garbled ：CID 字体乱码，需 OCR
    - scanned ：无/极少文本层，需 OCR
    """
    n = len(text)
    if pages > 0 and n < pages * 20:
        return "scanned", f"文本极少({n}字/{pages}页)，疑似扫描件"
    if n == 0:
        return "scanned", "无文本层"
    if _cjk_ratio(text) < 0.05 and _weird_ratio(text) > 0.15:
        return "garbled", f"中文占比{_cjk_ratio(text):.2%} 怪字符{_weird_ratio(text):.2%}"
    if _weird_ratio(text) > 0.35:
        return "garbled", f"怪字符占比{_weird_ratio(text):.2%}"
    return "ok", ""


# ---------------------------------------------------------------- 清洗

RE_PAGE_MARK = re.compile(r"^\s*[-—–]*\s*\d{1,4}\s*[-—–]*\s*$")
RE_WS = re.compile(r"[ \t\u00a0]+")
RE_MULTI_NL = re.compile(r"\n{3,}")


def strip_header_footer(pages: list[str]) -> list[str]:
    """去掉每页重复出现的行（页眉页脚）。

    原理：统计所有页首尾行的出现频次，出现 >=60% 且内容短（<=40字）的判为页眉页脚。
    """
    if len(pages) < 5:
        return pages
    from collections import Counter

    def cand(p: str) -> list[str]:
        lines = [l.strip() for l in p.splitlines() if l.strip()]
        return [l for l in (lines[:2] + lines[-2:]) if len(l) <= 40]

    counter: Counter = Counter()
    for p in pages:
        for l in set(cand(p)):
            counter[l] += 1
    thr = max(3, int(len(pages) * 0.6))
    bad = {l for l, c in counter.items() if c >= thr and not RE_ARTICLE.search(l)}
    if not bad:
        return pages
    out = []
    for p in pages:
        lines = [l for l in p.splitlines() if l.strip() not in bad and not RE_PAGE_MARK.match(l)]
        out.append("\n".join(lines))
    return out


_CN_END_PUNCT = "。；;！？?）”》】…．.｡"
_CN_START_PUNCT = "（(《“【"


def merge_broken_lines(text: str) -> str:
    """合并被 PDF 换行打断的中文句子。

    原理：中文行末若无结束标点，且下一行不是新条款/新编号开头，则视为断行，直接拼接。
    作用：让"第二百五十一条　井下使用的汽油……"这类条文恢复成完整句子，检索才准。
    """
    lines = [l.rstrip() for l in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append("")
            continue
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        prev_end = prev[-1] if prev else ""
        starts_new = bool(
            RE_ARTICLE.match(s)
            or RE_CHAPTER_CN.match(s)
            or RE_NUM_HEAD.match(s)
            or RE_CN_HEAD.match(s)
        )
        need_merge = (
            prev_end
            and prev_end not in _CN_END_PUNCT
            and not starts_new
            and len(prev) < 200
            and s[0] not in _CN_START_PUNCT
        )
        if need_merge:
            # 前一行是纯 ASCII/数字（多为表格残行）时另起一行，避免把表格糊成一团
            if sum(1 for c in prev[-8:] if "\u4e00" <= c <= "\u9fff") >= 2:
                out[-1] = prev + s
            else:
                out.append(s)
        else:
            out.append(s)
    merged = "\n".join(out)
    merged = RE_WS.sub(" ", merged)
    merged = RE_MULTI_NL.sub("\n\n", merged)
    return merged.strip()


def normalize_basic(text: str) -> str:
    """全角→半角、NFKC 归一、去零宽/软连字符。"""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_FULL2HALF)
    t = t.replace("\u00ad", "").replace("\u200b", "").replace("\ufeff", "")
    return t


def clean_document(pages: list[str]) -> str:
    """整档清洗：去页眉页脚 → 合并断行 → 基础归一。"""
    pages = strip_header_footer(pages)
    body = "\n".join(pages)
    body = merge_broken_lines(body)
    body = normalize_basic(body)
    return body
