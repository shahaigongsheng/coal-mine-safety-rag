"""探测候选语料：PDF 是否有文本层、条文模式长什么样。"""
import sys, re, pathlib

import fitz  # pymupdf

ROOT = pathlib.Path(r"E:\武志强知识库\raw")
PDFS = [
    "煤矿井下供电的三大保护细则.pdf",
    "煤矿井下机电设备完好性要求.pdf",
    "煤矿供电设计与继电保护整定计算示例(1).pdf",
    "GB 51145-2015 煤矿电气设备安装工程施工与验收规范.pdf",
    "煤矿重大事故隐患判定标准-2026-应急管理部令21号修订.pdf",
    "煤矿安全规程与解读/新版煤矿安全规程.pdf",
    "煤矿安全监控标准/AQ 1029-2019 煤矿安全监控系统及检测仪器使用管理规范.pdf",
    "架空乘人装置规范/AQ1038-2007 煤矿用架空乘人装置安全检验规范.pdf",
    "煤矿井下人员定位标准/AQ 1119-2023 煤矿井下人员定位系统通用技术条件.pdf",
]

ART_CN = re.compile(r"第\s*[〇零一二三四五六七八九十百千0-9]+\s*条")
for rel in PDFS:
    p = ROOT / rel
    if not p.exists():
        print(f"[MISSING] {rel}")
        continue
    try:
        doc = fitz.open(p)
    except Exception as e:
        print(f"[ERR] {rel}: {e}")
        continue
    n = doc.page_count
    # 取中间段文本（前面通常是封面/目录）
    mid = n // 3
    txt = ""
    for i in range(mid, min(mid + 6, n)):
        txt += doc[i].get_text()
    hits = len(ART_CN.findall(txt))
    sample = txt.replace("\n", " ")[:220]
    print(f"\n=== {rel}")
    print(f"    pages={n}  chars(mid-sample)={len(txt)}  条文标记数={hits}")
    print(f"    sample: {sample}")
    doc.close()
