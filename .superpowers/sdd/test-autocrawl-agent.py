"""全鏈路實測：跑 agent 問一個經驗問題，觀察 sparse 標記 + 背景補爬觸發。
用 Ohio State（osu，目前 0 筆）當對象。需 DB + OPENAI_API_KEY。
"""
import sys, time
sys.path.insert(0, "backend/scripts")

from retriever.agent import run_agent

print("=== 執行 agent（觀察 [Experience] 資料不足 與 [ExpCrawl] 背景補爬 log）===\n")
answer = run_agent("Ohio State 錄取的人 GPA 大概多少？", max_steps=5, verbose=True)

print("\n=== 最終答案（開頭應有『此校的申請經驗回報目前較少』提示）===")
print(answer)

print("\n=== 等背景 daemon thread 跑完補爬（不等的話行程結束會殺掉它）===")
time.sleep(40)
print("done")
