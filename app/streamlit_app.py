# -*- coding: utf-8 -*-
"""煤矿安全标准智能问答系统 —— Streamlit 前端。

运行：  python -m streamlit run app/streamlit_app.py
作用：  自然语言提问 → 返回条文原文 + 出处（文件名 / 标准号 / 条号 / 页码）
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from src.retrieve import Engine, tokenize  # noqa: E402

st.set_page_config(page_title="煤矿安全标准问答", page_icon="⛏️", layout="wide")

EXAMPLES = [
    "架空乘人装置的制动距离有什么要求？",
    "井下照明信号综合保护装置的作用是什么？",
    "煤矿井下漏电保护要满足什么要求？",
    "人员定位系统对下井人员唯一性检测怎么规定？",
    "第二百五十一条 井下存放汽油有什么规定？",
    "煤矿重大事故隐患有哪些情形？",
]


@st.cache_resource(show_spinner="正在加载条文索引 …")
def get_engine():
    e = Engine()
    return e


def highlight(text: str, query: str) -> str:
    """把命中的查询词高亮。

    原理：先分词取长度>=2 的词，按长度倒序替换，避免短词先吃掉长词。
    """
    t = html.escape(text)
    words = sorted({w for w in tokenize(query) if len(w) >= 2}, key=len, reverse=True)
    for w in words[:12]:
        try:
            t = re.sub(re.escape(html.escape(w)), lambda m: f"<mark>{m.group(0)}</mark>", t, flags=re.I)
        except re.error:
            continue
    return t.replace("\n", "<br>")


def main():
    try:
        eng = get_engine()
    except FileNotFoundError:
        st.error("未找到 data/clauses.jsonl，请先运行：`python -m src.build_index`")
        return

    st.title("⛏️ 煤矿安全标准智能问答系统")
    st.caption("自然语言提问 → 返回条文原文 + 出处（文件名 / 标准号 / 条号 / 页码）")

    stats = eng.stats()
    mode = eng.cfg["llm"]["provider"]

    # ------------------------------------------------------------ 侧栏
    with st.sidebar:
        st.subheader("索引状态")
        c1, c2 = st.columns(2)
        c1.metric("条文", f"{stats['clauses']:,}")
        c2.metric("文档", stats["files"])
        c3, c4 = st.columns(2)
        c3.metric("标准", stats["standards"])
        c4.metric("字数", f"{stats['chars']/10000:.1f}万")
        st.caption(f"回答模式：**{'检索+生成' if mode != 'none' else '纯检索'}**　"
                   f"向量检索：**{'开' if eng.emb_matrix is not None else '关'}**")

        st.divider()
        topk = st.slider("返回条文数", 3, 15, 6)
        files = ["（全部）"] + sorted({c["file_name"] for c in eng.clauses})
        pick = st.selectbox("限定文件", files)
        show_meta = st.checkbox("显示召回细节（BM25/向量排名）", value=False)
        st.divider()
        st.markdown("**示例问题**")
        for q in EXAMPLES:
            if st.button(q, key=f"ex_{q}", use_container_width=True):
                st.session_state["q"] = q

    # ------------------------------------------------------------ 主区
    default_q = st.session_state.get("q", "")
    query = st.text_input("输入你的问题", value=default_q, placeholder="例如：架空乘人装置的安全保护装置有哪些？")

    if not query:
        st.info("👈 左侧点一个示例问题，或直接输入。当前为**纯检索模式**：直接返回条文原文，不经过大模型改写，所以绝不会编造。")
        st.stop()

    filters = {"file_name": pick if pick != "（全部）" else None}
    with st.spinner("检索中 …"):
        res = eng.answer(query, topk=topk) if not filters["file_name"] else {
            "mode": "retrieval", "answer": "",
            "hits": eng.search(query, topk=topk, filters=filters),
        }

    if res.get("warn"):
        st.warning(res["warn"])
    if res.get("mode") == "rag" and res.get("answer"):
        st.subheader("📌 综合回答")
        st.markdown(res["answer"])

    hits = res["hits"]
    if not hits:
        st.error("未检索到相关条文。试试更具体的技术术语，或换个说法。")
        return

    st.subheader(f"📖 条文原文 · {len(hits)} 条")
    for i, h in enumerate(hits, 1):
        no = h.standard_no or ""
        title = f"[{i}] {no + ' · ' if no else ''}{h.clause_no}"
        with st.expander(f"{title}　—　{h.doc_title or h.file_name}", expanded=(i <= 3)):
            st.markdown(highlight(h.text, query), unsafe_allow_html=True)
            page = next((c.get("page", 0) for c in eng.clauses if c["clause_uid"] == h.clause_uid), 0)
            metabits = [
                f"**文件**：{h.file_name}",
                f"**页码**：第 {page} 页" if page else "",
                f"**章节**：{h.heading}" if h.heading else "",
            ]
            if show_meta:
                metabits.append(f"**召回**：{h.fuse_detail}　score={h.score}")
            st.caption("　|　".join([m for m in metabits if m]))

    st.divider()
    st.caption("答案依据来自本地知识库 `E:\\武志强知识库\\raw`，未经二次加工。"
               "工程应用请以现行正式出版文本为准。")


if __name__ == "__main__":
    main()
