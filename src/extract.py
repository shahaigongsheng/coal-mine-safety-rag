# -*- coding: utf-8 -*-
"""语料抽取：把 raw/ 里的原始文件转成带质量标记的纯文本缓存。

为什么单独一步：
  OCR 很慢（几百页的扫描件可能跑几十分钟），而"切条 + 建索引"需要反复调试。
  把抽取结果缓存到 data/extracted/ 之后，后续步骤几秒就能重跑。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src.parsers import parse_file  # noqa: E402
from src.clean import judge_quality  # noqa: E402


def doc_id_for(path: Path, root: Path) -> str:
    rel = str(path.relative_to(root)) if str(path).startswith(str(root)) else path.name
    return hashlib.md5(rel.encode("utf-8")).hexdigest()[:10]


def load_cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def extract_one(path: Path, cfg: dict, root: Path) -> dict:
    """抽取单文档，返回缓存结构。"""
    did = doc_id_for(path, root)
    cache = {
        "doc_id": did,
        "path": str(path),
        "rel_path": str(path.relative_to(root)) if str(path).startswith(str(root)) else path.name,
        "file_name": path.name,
        "ext": path.suffix.lower(),
        "title": "",
        "standard_no": "",
        "quality": "unsupported",
        "reason": "",
        "page_count": 0,
        "pages_len": [],
        "text": "",
    }
    d = parse_file(path, use_ocr=cfg["corpus"]["enable_ocr"],
                   max_pages=cfg["corpus"]["max_pdf_pages"])
    cache["quality"] = d.quality
    cache["reason"] = d.reason
    cache["page_count"] = d.page_count
    cache["pages_len"] = [len(p) for p in d.pages_raw]
    cache["title"] = d.title
    cache["standard_no"] = d.extra.get("standard_no", "")
    cache["text"] = d.text if d.usable else ""
    if cache["quality"] in ("garbled", "scanned") and cache["text"]:
        q, _ = judge_quality(cache["text"], max(1, cache["page_count"]))
        if q == "ok":
            cache["quality"] = "ocr-ok"
    return cache


def main(limit: int = 0, only_ocr: bool = False, force: bool = False,
         no_ocr: bool = False, paths_file: str = "", retry_failed: bool = False):
    cfg = load_cfg()
    if no_ocr:
        cfg["corpus"]["enable_ocr"] = False
    root = Path(cfg["corpus"]["root"])
    out_dir = ROOT / cfg["project"]["data_dir"] / "extracted"
    out_dir.mkdir(parents=True, exist_ok=True)

    from tools.scan_corpus import collect  # 复用候选清单逻辑

    files = collect(cfg)
    if only_ocr:
        keep = []
        inv_path = ROOT / cfg["project"]["data_dir"] / "inventory.json"
        if inv_path.exists():
            inv = json.loads(inv_path.read_text(encoding="utf-8"))
            need = {i["path"] for i in inv if i["quality"] in ("garbled", "scanned")}
            keep = [f for f in files if str(f) in need]
        files = keep
    if limit:
        files = files[:limit]
    paths_set = set()
    if paths_file:
        pf = Path(paths_file)
        names = [l.strip() for l in pf.read_text(encoding="utf-8").splitlines() if l.strip()]
        picked = []
        for n in names:
            p = root / n
            if p.exists():
                picked.append(p); paths_set.add(str(p))
            else:
                print(f"[warn] 不存在：{n}")
        if not only_ocr:
            files = files + [p for p in picked if p not in set(files)]
    if retry_failed:
        # 只重抽上次失败/异常的文件，且避开正在 OCR 的清单
        bad = []
        for fp in (ROOT / cfg["project"]["data_dir"] / "extracted").glob("*.json"):
            r = json.loads(fp.read_text(encoding="utf-8"))
            if (r.get("quality") in ("unsupported", "garbled", "scanned")
                    and str(r.get("path", "")) not in paths_set):
                bad.append(Path(r["path"]))
        files = [p for p in bad if p.exists()]

    print(f"待抽取 {len(files)} 份文档；enable_ocr={cfg['corpus']['enable_ocr']}")
    t0 = time.time()
    ok = fail = skip = 0
    for i, p in enumerate(files, 1):
        did = doc_id_for(p, root)
        fp = out_dir / f"{did}.json"
        if fp.exists() and not force:
            skip += 1
            continue
        r = extract_one(p, cfg, root)
        fp.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        if r["quality"] in ("ok", "ocr-ok", "ocr") and r["text"]:
            ok += 1
        else:
            fail += 1
        if i % 20 == 0:
            print(f"  {i}/{len(files)}  可用 {ok}  废 {fail}  跳过 {skip}  {time.time()-t0:.0f}s")
    print(f"\n完成：可用 {ok} / 不可用 {fail} / 已存在 {skip}，用时 {time.time()-t0:.0f}s")
    print(f"缓存目录：{out_dir}")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-ocr", action="store_true", help="只处理需要 OCR 的文件")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-ocr", dest="no_ocr", action="store_true", help="跳过 OCR，只抽取有文本层的部分（快速预热）")
    ap.add_argument("--paths-file", default="", help="只处理清单里的相对路径")
    ap.add_argument("--retry-failed", dest="retry_failed", action="store_true", help="只重抽上次失败的文件")
    a = ap.parse_args()
    main(limit=a.limit, only_ocr=a.only_ocr, force=a.force,
         no_ocr=a.no_ocr, paths_file=a.paths_file, retry_failed=a.retry_failed)
