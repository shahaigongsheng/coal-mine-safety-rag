# -*- coding: utf-8 -*-
"""扫描候选语料，产出 data/inventory.json。

作用：先摸清家底 —— 哪些文件能直读、哪些必须 OCR、哪些作废。
     只读不写：本脚本绝对不修改 raw/ 下的任何原始文件。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.parsers import parse_file  # noqa: E402

import yaml  # noqa: E402

EXTS = {".pdf", ".docx", ".doc", ".md", ".txt"}


def collect(cfg: dict) -> list[Path]:
    root = Path(cfg["corpus"]["root"])
    files: list[Path] = []
    for d in cfg["corpus"]["include_dirs"]:
        base = root / d
        if not base.exists():
            print(f"[warn] 目录不存在: {base}")
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.suffix.lower() in EXTS:
                rel = p.relative_to(root).parts[0]
                if rel in cfg["corpus"]["exclude_dirs"]:
                    continue
                files.append(p)
    for f in cfg["corpus"]["include_files"]:
        p = root / f
        if p.exists():
            files.append(p)
        else:
            print(f"[warn] 文件不存在: {f}")
    # 去重
    seen, out = set(), []
    for p in files:
        if str(p) not in seen:
            seen.add(str(p))
            out.append(p)
    return out


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    files = collect(cfg)
    print(f"候选文件 {len(files)} 个，开始扫描（不 OCR）...\n")

    inv, t0 = [], time.time()
    for i, p in enumerate(files, 1):
        # 扫描阶段关闭 OCR，只判断"能不能直读"
        d = parse_file(p, use_ocr=False, max_pages=cfg["corpus"]["max_pdf_pages"])
        inv.append({
            "path": str(p),
            "file_name": d.file_name,
            "ext": d.ext,
            "title": d.title,
            "standard_no": d.extra.get("standard_no", ""),
            "quality": d.quality,
            "reason": d.reason,
            "pages": d.page_count,
            "chars": d.char_count,
        })
        if i % 50 == 0 or i == len(files):
            ok = sum(1 for x in inv if x["quality"] == "ok")
            print(f"  {i}/{len(files)}  直读可用 {ok}  用时 {time.time()-t0:.0f}s")

    data_dir = ROOT / cfg["project"]["data_dir"]
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "inventory.json").write_text(
        json.dumps(inv, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    from collections import Counter
    cnt = Counter(x["quality"] for x in inv)
    print("\n=== 语料清单 ===")
    for k, v in cnt.most_common():
        print(f"  {k:12s} {v:5d} 个")
    print(f"\n已写出 {data_dir/'inventory.json'}")
    print("\n需 OCR 的文件（前 20）：")
    for x in [i for i in inv if i["quality"] in ("garbled", "scanned")][:20]:
        print(f"  [{x['quality']:8s}] {x['file_name'][:60]:60s} {x['reason'][:40]}")


if __name__ == "__main__":
    main()
