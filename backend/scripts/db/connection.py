"""共用：專案根目錄、載入 .env、取得連線。"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

# 專案路徑
# __file__是當前檔案path
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
PROJECT_ROOT = BACKEND_ROOT.parent

# backend/.env要自己建立
load_dotenv(BACKEND_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    if not DATABASE_URL:
        return None
    try:
        return psycopg.connect(DATABASE_URL)
    except Exception as e:
        print(f"連線錯誤: {e}")
        return None
