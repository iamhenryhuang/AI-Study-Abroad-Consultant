import requests
from parsel import Selector

url = "https://www.cs.cmu.edu/academics/masters/programs"

# 偽裝
headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers, timeout=20)

print("=== Step 1: Fetch Page ===")
print("Status code:", response.status_code ,"   200 代表正常回應")
print("Final URL:", response.url)
print("Content-Type:", response.headers.get("Content-Type"))

sel = Selector(response.text)
#從CS擷取標題
title = sel.css("title::text").get()

print("Page title:", title)
print("HTML length:", len(response.text))