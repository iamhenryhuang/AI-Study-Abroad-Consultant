# analyze_results.py
import json
from collections import defaultdict

def load_results(path="results.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

# ──────────────────────────────────────────────
# 核心統計
# ──────────────────────────────────────────────

def analyze(results: list):
    total = len(results)

    by_type = defaultdict(list)
    for r in results:
        by_type[r.get("type", "unknown")].append(r)

    effective_count = defaultdict(int)
    for r in results:
        matched = r.get("matched_types", [])
        if matched:
            for m in matched:
                sub = m.get("type", "")
                if sub:
                    effective_count[sub] += 1
        else:
            t = r.get("type", "unknown")
            effective_count[t] += 1

    score_dist = {}
    TARGET_TYPES = ["admissions", "faq", "program", "tuition", "other", "error"]
    for t in TARGET_TYPES:
        items  = by_type.get(t, [])
        scores = [r["best_score"] for r in items if r.get("best_score") is not None]
        if not scores:
            continue
        scores.sort()
        n = len(scores)
        score_dist[t] = {
            "count"  : n,
            "min"    : round(scores[0], 2),
            "max"    : round(scores[-1], 2),
            "mean"   : round(sum(scores) / n, 2),
            "median" : round(scores[n // 2], 2),
            "p25"    : round(scores[int(n * 0.25)], 2),
            "p75"    : round(scores[int(n * 0.75)], 2),
            "p90"    : round(scores[int(n * 0.90)], 2),
        }

    raw_score_dist = {}
    for pt in ["admissions", "faq", "program", "tuition"]:
        all_scores = [
            r["scores"][pt]
            for r in results
            if "scores" in r and pt in r["scores"]
        ]
        all_scores.sort()
        n = len(all_scores)
        if n == 0:
            continue
        raw_score_dist[pt] = {
            "count" : n,
            "min"   : round(all_scores[0], 2),
            "max"   : round(all_scores[-1], 2),
            "mean"  : round(sum(all_scores) / n, 2),
            "p50"   : round(all_scores[n // 2], 2),
            "p75"   : round(all_scores[int(n * 0.75)], 2),
            "p90"   : round(all_scores[int(n * 0.90)], 2),
            "p95"   : round(all_scores[int(n * 0.95)], 2),
        }

    return total, by_type, effective_count, score_dist, raw_score_dist


# ──────────────────────────────────────────────
# 印出報告
# ──────────────────────────────────────────────

def print_report(total, by_type, effective_count, score_dist, raw_score_dist, current_thresholds=None):
    W = 66
    print("\n" + "═" * W)
    print("  📊 分類結果統計報告")
    print("═" * W)
    print(f"  總 URL 數：{total}")
    print(f"  ※ 同一 URL 若超過多個門檻，各類別均計一次\n")

    SHOW_ORDER = ["admissions", "faq", "program", "tuition", "other", "error"]
    eff_total  = sum(effective_count[t] for t in SHOW_ORDER)

    print("  ┌─ 各分類數量（超過門檻即計入）" + "─" * (W - 31) + "┐")
    print(f"  │  {'type':<14} {'count':>6}  {'佔總URL%':>9}  bar")
    print("  │  " + "-" * (W - 5))
    for t in SHOW_ORDER:
        cnt = effective_count.get(t, 0)
        pct = cnt / total * 100 if total else 0
        bar = "█" * int(pct / 2)
        print(f"  │  {t:<14} {cnt:>6}  ({pct:5.1f}%)  {bar}")
    for t, cnt in effective_count.items():
        if t not in SHOW_ORDER:
            print(f"  │  {t:<14} {cnt:>6}")
    print("  │")
    print(f"  │  ※ 各類別加總 = {eff_total}（可 > 總URL數，因同一URL可跨類別）")
    print("  └" + "─" * (W - 2) + "┘\n")

    print("  ┌─ best_score 分布（依原始 type）" + "─" * (W - 33) + "┐")
    print(f"  │  {'type':<14} {'n':>5}  {'min':>6}  {'p25':>6}  {'med':>6}  {'p75':>6}  {'p90':>6}  {'max':>6}")
    print("  │  " + "-" * (W - 5))
    for t in SHOW_ORDER:
        d = score_dist.get(t)
        if not d:
            continue
        print(f"  │  {t:<14} {d['count']:>5}  {d['min']:>6}  {d['p25']:>6}  {d['median']:>6}  {d['p75']:>6}  {d['p90']:>6}  {d['max']:>6}")
    print("  └" + "─" * (W - 2) + "┘\n")

    print("  ┌─ 各類別原始得分分布（所有 URL）" + "─" * (W - 33) + "┐")
    print(f"  │  {'category':<14} {'n':>5}  {'min':>6}  {'p50':>6}  {'p75':>6}  {'p90':>6}  {'p95':>6}  {'max':>6}")
    print("  │  " + "-" * (W - 5))
    for pt, d in raw_score_dist.items():
        thr = f"  ← 目前={current_thresholds[pt]}" if current_thresholds and pt in current_thresholds else ""
        print(f"  │  {pt:<14} {d['count']:>5}  {d['min']:>6}  {d['p50']:>6}  {d['p75']:>6}  {d['p90']:>6}  {d['p95']:>6}  {d['max']:>6}{thr}")
    print("  └" + "─" * (W - 2) + "┘\n")

    print("  ┌─ Threshold 設置建議 " + "─" * (W - 22) + "┐")
    print("  │  p75 → 寬鬆（召回率高）   p90 → 嚴格（精確率高）")
    print("  │")
    for pt, d in raw_score_dist.items():
        thr_now = f"  目前={current_thresholds[pt]}" if current_thresholds and pt in current_thresholds else ""
        print(f"  │  {pt:<16} 寬鬆≈{d['p75']:>6}   嚴格≈{d['p90']:>6}{thr_now}")
    print("  │")
    print("  └" + "─" * (W - 2) + "┘\n")


# ──────────────────────────────────────────────
# 輸出各分類 URL 清單
# ──────────────────────────────────────────────

def export_classified_urls(results, path="classified_urls.txt"):
    """
    依 matched_types 展開，每個超過門檻的類別都列出對應 URL。
    other / error 的 matched_types 為空，直接用 type 欄位。
    格式：
        admissions:
        https://...
        https://...

        faq:
        https://...
    """
    SHOW_ORDER = ["admissions", "faq", "program", "tuition", "other", "error"]

    # 收集每個類別的 URL（用 set 去重，再轉 list 保持插入順序）
    category_urls: dict[str, list] = {t: [] for t in SHOW_ORDER}
    seen:          dict[str, set]  = {t: set() for t in SHOW_ORDER}

    for r in results:
        url     = r.get("url", "")
        matched = r.get("matched_types", [])

        if matched:
            for m in matched:
                cat = m.get("type", "")
                if cat in category_urls and url not in seen[cat]:
                    category_urls[cat].append(url)
                    seen[cat].add(url)
        else:
            # other / error
            cat = r.get("type", "unknown")
            if cat not in category_urls:
                category_urls[cat] = []
                seen[cat]          = set()
            if url not in seen[cat]:
                category_urls[cat].append(url)
                seen[cat].add(url)

    # 寫檔
    with open(path, "w", encoding="utf-8") as f:
        for cat in SHOW_ORDER:
            urls = category_urls.get(cat, [])
            f.write(f"{cat}:  ({len(urls)} URLs)\n")
            for url in urls:
                f.write(f"{url}\n")
            f.write("\n")

        # SHOW_ORDER 以外的類別（如有）
        for cat, urls in category_urls.items():
            if cat not in SHOW_ORDER and urls:
                f.write(f"{cat}:  ({len(urls)} URLs)\n")
                for url in urls:
                    f.write(f"{url}\n")
                f.write("\n")

    print(f"📄 分類 URL 清單已儲存至 {path}")


# ──────────────────────────────────────────────
# 輸出詳細 CSV
# ──────────────────────────────────────────────

def export_csv(results, path="results_detail.csv"):
    import csv
    score_keys = sorted(results[0]["scores"].keys()) if results and "scores" in results[0] else []
    fieldnames = (
        ["school_id", "url", "type", "matched_types", "best_type", "best_score"]
        + score_keys
        + ["title", "error"]
    )
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in results:
            row = {k: r.get(k, "") for k in ["school_id", "url", "type", "best_type", "best_score", "title", "error"]}
            row["matched_types"] = ",".join(
                m["type"] for m in r.get("matched_types", []) if m.get("type")
            )
            for k in score_keys:
                row[k] = r.get("scores", {}).get(k, "")
            writer.writerow(row)
    print(f"📄 詳細 CSV 已儲存至 {path}")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

if __name__ == "__main__":
    from setting.parameter import THRESHOLDS

    results = load_results("results.json")
    total, by_type, effective_count, score_dist, raw_score_dist = analyze(results)
    print_report(total, by_type, effective_count, score_dist, raw_score_dist, current_thresholds=THRESHOLDS)
    export_classified_urls(results)          # ← 輸出分類 URL 清單
    # export_csv(results)                    # ← 需要時開啟
"""

輸出的 `classified_urls.txt` 格式如下：
```
admissions:  (151 URLs)
https://www.ntu.edu.tw/admissions/
https://admission.nthu.edu.tw/...
...

faq:  (67 URLs)
https://www.ntu.edu.tw/faq/
...

program:  (210 URLs)
...

tuition:  (89 URLs)
...

other:  (301 URLs)
...

error:  (5 URLs)
"""