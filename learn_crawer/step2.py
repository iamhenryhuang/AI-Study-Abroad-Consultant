import requests
from parsel import Selector

url = "https://www.cs.cmu.edu/academics/masters/programs"

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers, timeout=20)

print("=== Step 2: Extract Visible Text ===")
print("Status code:", response.status_code)

# Wrapper for input data in HTML, JSON, or XML format, 
# hat allows selecting parts of it using selection expressions
sel = Selector(response.text)

# 選擇CSS標籤
title = sel.css("title::text").get()
print("Page title:", title)

# 從整個 HTML 的 <body> 裡，抓出所有「不是空白的文字節點」，並回傳成 list

# // = anywhere（不管層級）body = HTML body 標籤
# //text() 在 <body> 裡面，找「所有文字節點」
# normalize-space() 只找去掉空白後還是有內容的節點
# texts是一個list，每個元素都是一個字串 ish sijfoiw ewi
texts = sel.xpath("//body//text()[normalize-space()]").getall()

# 清掉多餘空白，並串在一起
# t.split() = 自動用「任何空白」切割（空格、tab、換行），把多個空白當成一個，移除前後空白
# t => [hello , world ,me]的陣列
# join => "hello world me"的字串
### t.strip() 移除「前後空白」
cleaned = []
for t in texts:
    t = " ".join(t.split())
    if t:
        cleaned.append(t)

print("\n=== First 50 text nodes ===")
for i, t in enumerate(cleaned[:50], 1):
    print(f"{i:02d}. {t}")

full_text = " ".join(cleaned)

print("\n=== First 1000 characters of full text ===")
print(full_text[:2000])

print("\nTotal text nodes:", len(cleaned))
print("Full text length:", len(full_text))