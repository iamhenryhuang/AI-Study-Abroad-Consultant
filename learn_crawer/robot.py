import requests

url = "https://www.cs.stanford.edu/robots.txt"

headers = {
    "User-Agent": "Mozilla/5.0"
}

response = requests.get(url, headers=headers, timeout=20)

print("=== Step 5.1: Raw robots.txt ===")
print("Status code:", response.status_code)
print("Final URL:", response.url)

print("\n=== robots.txt content ===")
print(response.text[:2000])  # 先印前2000字