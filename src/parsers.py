# -*- coding: utf-8 -*-
"""文档解析器：PDF / DOCX / DOC / MD → 逐页文本 + 质量标记。

设计原则：**格式无关**。
上层只拿到 `pages: list[str]`，至于这篇是扫描件、乱码件还是 Word，由 quality 字段说明。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pymupdf  # PyMuPDF

from .clean import clean_document, judge_quality

# 可选 OCR（rapidocr-onnxruntime 安装后自动启用）
try:  # pragma: no cover
    from rapidocr_onnxruntime import RapidOCR

    _OCR = RapidOCR()
except Exception:  # OCR 未安装时降级为不支持
    _OCR = None


@dataclass
class ParsedDoc:
    """一份文档解析后的统一形态。"""

    path: str
    file_name: str
    ext: str
    title: str = ""
    pages_raw: list[str] = field(default_factory=list)
    quality: str = "unknown"  # ok | garbled | scanned | ocr  | unsupported
    reason: str = ""
    page_count: int = 0
    char_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return clean_document(self.pages_raw) if self.pages_raw else ""

    @property
    def usable(self) -> bool:
        return self.quality in ("ok", "ocr") and self.char_count > 0


# ------------------------------------------------------------------ 标准号识别

RE_STD_NO = re.compile(
    r"\b(GB[\s/_]*T?[\s/_]*\d{4,5}(?:\.\d+)?[\s/_\-]*\d{0,4}"
    r"|AQ[\s/_]*\d{3,5}[\s/_\-]*\d{0,4}"
    r"|MT[\s/_]*T?[\s/_]*\d{3,5}[\s/_\-]*\d{0,4}"
    r"|KA[\s/_]*\d{2,4}[\s/_\-]*\d{0,4}"
    r"|NB[\s/_]*T?[\s/_]*\d{3,5}[\s/_\-]*\d{0,4}"
    r"|JJF[\s/_]*\d{3,5}"
    r"|DL[\s/_]*T?[\s/_]*\d{3,5})\b",
    re.I,
)

_NOISE_TOKENS = ("最终版", "修改", "副本", "打印", "副本(", "新版", "解读", "发布", "试行", "修订")


def extract_standard_no(name: str) -> str:
    """从文件名抽出标准号。作用：让出处能同时显示「标准号 + 条号」，这是煤矿工程师最想要的答案格式。"""
    m = RE_STD_NO.search(name)
    if not m:
        return ""
    raw = m.group(0).replace("_", " ").replace("/", " ")
    parts = raw.split()
    if len(parts) >= 2:
        return f"{parts[0].upper()} {parts[1]}"
    return raw.upper()


def title_from_filename(name: str) -> str:
    """文件名 → 人类可读标题（去扩展名、去括号编号噪声）。"""
    stem = Path(name).stem
    stem = re.sub(r"\(\d+\)$", "", stem).strip()
    stem = re.sub(r"^\d+[、.．\s]*", "", stem).strip()
    m = RE_STD_NO.search(stem)
    if m:
        rest = stem[m.end():].strip(" _-—（）()")
        if rest and not any(t in rest for t in _NOISE_TOKENS):
            return rest
        return stem.strip()
    return stem


# ------------------------------------------------------------------ PDF

def _pdf_pages(path: Path) -> list[str]:
    doc = pymupdf.open(str(path))
    try:
        return [p.get_text("text") for p in doc]
    finally:
        doc.close()


def _pdf_ocr(path: Path, dpi: int = 200) -> list[str]:
    """逐页渲染 → OCR。作用：救回扫描件与 CID 乱码 PDF（工业标准里占比很高）。"""
    if _OCR is None:
        return []
    doc = pymupdf.open(str(path))
    pages: list[str] = []
    try:
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            img = pix.tobytes("png")
            res, _ = _OCR(img)
            pages.append("\n".join(item[1] for item in res) if res else "")
    finally:
        doc.close()
    return pages


def parse_pdf(path: Path, use_ocr: bool = True, max_pages: int = 0) -> ParsedDoc:
    pages = _pdf_pages(path)
    if max_pages:
        pages = pages[:max_pages]
    joined = "".join(pages)
    q, reason = judge_quality(joined, len(pages))
    pd_ = ParsedDoc(
        path=str(path),
        file_name=path.name,
        ext=".pdf",
        pages_raw=pages,
        page_count=len(pages),
        char_count=len(joined),
    )
    if q == "ok":
        pd_.quality, pd_.reason = "ok", ""
        return pd_
    # 坏文本 → 尝试 OCR
    if use_ocr and _OCR is not None:
        pd_.reason = reason
        if max_pages:
            ocred = _pdf_ocr(path)[:max_pages]
        else:
            ocred = _pdf_ocr(path)
        if ocred and sum(len(x) for x in ocred) > len(joined):
            pd_.pages_raw = ocred
            pd_.quality = "ocr"
            pd_.char_count = sum(len(x) for x in ocred)
            return pd_
    pd_.quality = q
    pd_.reason = reason
    return pd_


# ------------------------------------------------------------------ DOCX

def parse_docx(path: Path) -> ParsedDoc:
    import docx  # python-docx

    d = docx.Document(str(path))
    lines = [p.text for p in d.paragraphs]
    # Word 表格对规范极其常见（限值表、参数表），必须保留
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    text = "\n".join(lines)
    # docx 没有"页"概念，按 ~1500 字虚拟分页，供上层做统一处理
    pages = [text[i: i + 1500] for i in range(0, len(text), 1500)] or [""]
    return ParsedDoc(
        path=str(path),
        file_name=path.name,
        ext=".docx",
        pages_raw=pages,
        quality="ok" if len(text) > 50 else "scanned",
        reason="" if len(text) > 50 else "docx 无正文",
        page_count=len(pages),
        char_count=len(text),
    )


# ------------------------------------------------------------------ DOC（二进制兜底）

def parse_doc_binary(path: Path) -> ParsedDoc:
    """.doc（Word 97-2003）兜底解析。

    原理：本机没装 Word/LibreOffice，无法格式转换。退而求其次读 OLE 复合文档的
    WordDocument 流，按 UTF-16 抽取可见文字。
    作用：能救回大部分正文，表格/格式会丢；标记为 ocr 级别质量，可后续 OCR 升级。
    """
    try:
        import olefile
    except ImportError:
        return ParsedDoc(str(path), path.name, ".doc", quality="unsupported", reason="未安装 olefile")
    if not olefile.isOleFile(str(path)):
        return ParsedDoc(str(path), path.name, ".doc", quality="unsupported", reason="非 OLE 容器")
    ole = olefile.OleFileIO(str(path))
    text = ""
    try:
        if ole.exists("WordDocument"):
            data = ole.openstream("WordDocument").read()
            # 粗略：取流后半段尝试 UTF-16LE 解码，过滤控制符
            for offset in (0x0800, 0x1000, 0x1800):
                if offset < len(data):
                    chunk = data[offset:]
                    try:
                        dec = chunk.decode("utf-16-le", errors="ignore")
                    except Exception:
                        continue
                    clean = "".join(
                        ch for ch in dec
                        if ch == "\n" or ch == "\r" or ch == "\t" or 0x20 <= ord(ch) < 0xFFFE
                    )
                    clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", clean)
                    if len(clean) > len(text):
                        text = clean
    finally:
        ole.close()
    text = text.strip()
    pages = [text[i: i + 1500] for i in range(0, len(text), 1500)] or [""]
    return ParsedDoc(
        path=str(path),
        file_name=path.name,
        ext=".doc",
        pages_raw=pages,
        quality="ok" if len(text) > 200 else "scanned",
        reason="" if len(text) > 200 else "二进制流无可用正文",
        page_count=len(pages),
        char_count=len(text),
    )


# ------------------------------------------------------------------ MD / TXT

def parse_text(path: Path) -> ParsedDoc:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            text = path.read_text(encoding=enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:
        return ParsedDoc(str(path), path.name, path.suffix.lower(), quality="unsupported", reason="编码未知")
    pages = [text[i: i + 1500] for i in range(0, len(text), 1500)] or [""]
    return ParsedDoc(
        path=str(path),
        file_name=path.name,
        ext=path.suffix.lower(),
        pages_raw=pages,
        quality="ok" if len(text.strip()) > 50 else "scanned",
        page_count=len(pages),
        char_count=len(text),
    )


_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".doc": parse_doc_binary,
    ".md": parse_text,
    ".txt": parse_text,
}


def parse_file(path: str | Path, use_ocr: bool = True, max_pages: int = 0) -> ParsedDoc:
    p = Path(path)
    fn = _PARSERS.get(p.suffix.lower())
    if fn is None:
        return ParsedDoc(str(p), p.name, p.suffix.lower(), quality="unsupported", reason="不支持的格式")
    try:
        pd_ = fn(p, use_ocr=use_ocr, max_pages=max_pages) if p.suffix.lower() == ".pdf" else fn(p)
    except Exception as e:  # 单文件失败不拖垮整批
        return ParsedDoc(str(p), p.name, p.suffix.lower(), quality="unsupported", reason=f"{type(e).__name__}: {e}")
    pd_.title = title_from_filename(p.name)
    pd_.extra["standard_no"] = extract_standard_no(p.name)
    return pd_
