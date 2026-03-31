import hashlib
import json
import os
from pathlib import Path
from typing import List, Dict, Any

# 輸出資料夾，可依需求修改
OUTPUT_DIR = "school_data"


def save_school_results(results: List[Dict[str, Any]], school_id: str, output_dir: str = OUTPUT_DIR) -> None:
    """
    將單一學校的分類結果儲存到 JSON 檔案。

    規則：
    - 檔案命名為 {school_id}_data.json
    - 只保留通過門檻（matched_types 不為空）且 type != "other" 的網頁
    - 每筆記錄包含：school_id、url、passed_types、data（可見文字）
    - 每次執行都直接覆蓋舊檔
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    records = []
    seen_hashes: set = set()   # content-hash deduplication within this school

    for result in results:
        if result.get("error"):
            continue

        matched_types = result.get("matched_types", [])
        if not matched_types:
            continue

        if result.get("type") == "other":
            continue

        passed_types = [
            {"type": m["type"], "score": m["score"]}
            for m in matched_types
        ]

        visible_text = result.get("full_text") or result.get("text_preview", "")

        # Skip pages whose content is byte-for-byte identical to one already saved.
        # This catches SPA sites that serve the same nav-only blob for every URL.
        data_hash = hashlib.md5(
            visible_text.encode("utf-8", errors="replace")
        ).hexdigest()
        if data_hash in seen_hashes:
            print(f"  ⏭  [{school_id}] duplicate content skipped: {result.get('url','')[:80]}")
            continue
        seen_hashes.add(data_hash)

        records.append({
            "school_id": school_id,
            "url": result.get("url", ""),
            "passed_types": passed_types,
            "data": visible_text,
            "data_hash": data_hash,
        })

    if not records:
        print(f"⚠️  [{school_id}] 沒有符合條件的頁面，不建立檔案")
        return

    output_path = Path(output_dir) / f"{school_id}_data.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"💾 [{school_id}] 覆蓋儲存 {len(records)} 筆 → {output_path}")


def save_single_result(result: Dict[str, Any], output_dir: str = OUTPUT_DIR) -> None:
    """
    即時儲存單筆結果（可在 worker 迴圈內每抓一頁就呼叫一次）。
    """
    school_id = result.get("school_id", "unknown")
    save_school_results([result], school_id=school_id, output_dir=output_dir)

