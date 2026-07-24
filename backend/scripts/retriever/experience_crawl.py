"""申請經驗背景補爬：agent 發現某校 applicant_reports 資料太少時，
在背景（daemon thread）爬 GradCafe 補該校資料 upsert 進 DB，不阻塞使用者回應。

跨目錄的 crawl_query / _clean_gradcafe / _INSERT_SQL 以 importlib 從檔案路徑
延遲載入，避開 repo 根 db/ 與 backend/scripts/db/ 的 namespace 撞名。
"""
from __future__ import annotations

import importlib.util
import threading
from datetime import datetime, timedelta
from pathlib import Path

from db.connection import get_connection

SPARSE_THRESHOLD = 5          # 少於此筆數視為「資料不足」
_CRAWL_MAX_PAGES = 5
_DEDUP_DAYS = 7

_ROOT = Path(__file__).resolve().parents[3]   # repo 根
_in_flight: set[str] = set()
_lock = threading.Lock()
_deps = None                  # (crawl_query, _clean_gradcafe, _INSERT_SQL)


def _load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _get_deps():
    """延遲載入跨目錄函式，首次呼叫時才載，避免模組 import 時的重依賴與副作用。"""
    global _deps
    if _deps is None:
        gradcafe = _load_module("gradcafe_crawler", "crawler/gradcafe.py")
        lar = _load_module("load_applicant_reports", "db/load_applicant_reports.py")
        _deps = (gradcafe.crawl_query, lar._clean_gradcafe, lar._INSERT_SQL)
    return _deps


def _school_to_gradcafe_query(school_id: str) -> str:
    """把 school_id 轉成適合 GradCafe（英文站）搜尋的字串：取最長的英文別名。

    _SCHOOL_ALIASES 於函式內延遲 import：避免模組載入時觸發 retriever.agent 套件
    初始化，與 nodes/retrieval.py 對本模組的 import 形成循環。
    """
    from retriever.agent.state import _SCHOOL_ALIASES
    aliases = _SCHOOL_ALIASES.get(school_id, [])
    english = [a for a in aliases if a.isascii()]
    return max(english, key=len) if english else school_id


def _recently_crawled(school_id: str, days: int = _DEDUP_DAYS) -> bool:
    """該校最近 days 天內是否已有補爬紀錄（以 applicant_reports.created_at 最大值判斷）。"""
    conn = get_connection()
    if not conn:
        return False
    try:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT MAX(created_at) FROM applicant_reports WHERE school_id = %s",
                (school_id,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    last = row[0] if row else None
    if last is None:
        return False
    return (datetime.now() - last) < timedelta(days=days)


def _crawl_and_load(school_id: str) -> None:
    """背景 thread 主體：爬該校 GradCafe → 清洗 → upsert。失敗只記 log。"""
    try:
        crawl_query, clean_gradcafe, insert_sql = _get_deps()
        query = _school_to_gradcafe_query(school_id)
        entries = crawl_query(query, max_pages=_CRAWL_MAX_PAGES)
        records = clean_gradcafe(entries)
        if not records:
            print(f"[ExpCrawl] {school_id} 補爬無新資料")
            return
        conn = get_connection()
        if not conn:
            print("[ExpCrawl] 無法取得資料庫連線")
            return
        try:
            with conn.cursor() as cur:
                for rec in records:
                    cur.execute(insert_sql, rec)
            conn.commit()
            print(f"[ExpCrawl] {school_id} 補爬完成，upsert {len(records)} 筆")
        finally:
            conn.close()
    except Exception as e:
        print(f"[ExpCrawl] {school_id} 補爬失敗：{e}")
    finally:
        with _lock:
            _in_flight.discard(school_id)


def maybe_enqueue_crawl(school_id: str) -> None:
    """若該校未在進行中、且近 7 天未爬過，開一個背景 daemon thread 補爬。永不 raise。"""
    if not school_id:
        return
    try:
        with _lock:
            if school_id in _in_flight:
                return
        if _recently_crawled(school_id):
            return
        with _lock:
            if school_id in _in_flight:      # DB 查詢期間可能已被別的請求加入
                return
            _in_flight.add(school_id)
        threading.Thread(target=_crawl_and_load, args=(school_id,), daemon=True).start()
    except Exception as e:
        print(f"[ExpCrawl] enqueue 失敗：{e}")
