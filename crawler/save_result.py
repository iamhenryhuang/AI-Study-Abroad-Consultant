import json
import os
from pathlib import Path
from typing import List, Dict, Any

# 輸出資料夾，可依需求修改
OUTPUT_DIR = "school_data"


def save_school_results(results: List[Dict[str, Any]], output_dir: str = OUTPUT_DIR) -> None:
    """
    將分類結果依學校分組，儲存到各自的 JSON 檔案。

    規則：
    - 每間學校一個檔案，命名為 {school_id}_data.json
    - 只保留通過門檻（matched_types 不為空）且 type != "other" 的網頁
    - 每筆記錄包含：school_id、url、passed_types、data（可見文字）
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # 依 school_id 分組
    school_map: Dict[str, List[Dict[str, Any]]] = {}
    for result in results:
        school_id = result.get("school_id", "unknown")
        school_map.setdefault(school_id, []).append(result)

    for school_id, school_results in school_map.items():
        records = []

        for result in school_results:
            # 跳過錯誤頁
            if result.get("error"):
                continue

            # 取得通過門檻的 type 列表
            matched_types = result.get("matched_types", [])  # list of {"type": ..., "score": ...}

            # 丟棄沒有任何通過門檻的頁面
            if not matched_types:
                continue

            # 丟棄最終分類為 "other" 的頁面
            if result.get("type") == "other":
                continue

            # 收集所有通過門檻的 type（不只最佳）
            passed_types = [
                {"type": m["type"], "score": m["score"]}
                for m in matched_types
            ]

            # 使用現有的可見文字（已包含下拉捲動後的內容）
            visible_text = result.get("full_text") or result.get("text_preview", "")

            record = {
                "school_id": school_id,
                "url":        result.get("url", ""),
                "passed_types": passed_types,
                "data":       visible_text,
            }
            records.append(record)

        if not records:
            continue  # 該學校沒有任何符合條件的頁面，不建立檔案

        output_path = Path(output_dir) / f"{school_id}_data.json"

        # 若檔案已存在，合併舊資料（避免重複 URL）
        existing: List[Dict] = []
        if output_path.exists():
            try:
                with open(output_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing = []

        # 用 url 去重
        existing_urls = {r["url"] for r in existing}
        new_records = [r for r in records if r["url"] not in existing_urls]
        merged = existing + new_records

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)

        print(f"💾 [{school_id}] 儲存 {len(new_records)} 筆（共 {len(merged)} 筆）→ {output_path}")


def save_single_result(result: Dict[str, Any], output_dir: str = OUTPUT_DIR) -> None:
    """
    即時儲存單筆結果（可在 worker 迴圈內每抓一頁就呼叫一次）。
    """
    save_school_results([result], output_dir=output_dir)