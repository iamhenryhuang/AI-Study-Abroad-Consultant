import json
import os
import glob
import re
from pathlib import Path

# ============================================================
# 在這裡自由新增要刪除的字眼
# Add any keywords/phrases you want to remove here
# ============================================================
KEYWORDS_TO_REMOVE = [
    "Connect with us",
    "Notifications",
    "My Favorites",
    "Settings",
    "setting",
    "Sign Out",
    "Sign In",
    "Committee Information",
    "Go.Grad",
    "SmartRecs",
    "Add funding awards to your favorites list",
    "Get notified of upcoming deadlines and events",
    "Receive personalized recommendations for funding awards",
    "Favorites, recommendations, and notifications are only available for UCLA Graduate Students at this time.",
    "UCLA will never share your email address and you may unsubscribe at any time.",
    "Sign Up",
    "Skip to content",
    "search grad.ucla",
    "Connect with us and learn about our services on the Graduate Education Portal",
    "Notifications",
    "Contact Us",
    "Twitter",
    "Instagram",
    "YouTube",
    "height",
    "Width",
    "style",
    "none",
    "hidden",
    "visibility",
    "true",
    "iframe",
    "<",">",
    "src",
    "Skip to main content",
    "Skip to secondary navigation",
    "(link is external)",
    "Skip to Content Top Home About Undergraduate Graduate Partnerships & Research News & Events People Contact Us",
    "Your voice matters! Graduate students—tell us about your experience at U of T. ",
    "Your voice matters",
    "Skip to main content <iframe src=\"https://www.googletagmanager.com/ns.html?id=GTM-TZKZB3\" height=\"0\" width=\"0\" style=\"display:none;visibility:hidden\"></iframe>",
    "Toggle",
    "navigation",
    "submenu",
    "Search",
    "Check your email to complete the gradSERU survey for a chance to win one of seven $3,000 bursaries! ",
    "Skip to main  =\"https://www.googletagmanager.com/ns.html?id=GTM-N7QFGH2P\" =\"0\" =\"0\" =\"display:;",
    ":\"/ Graduate Education Office of Graduate and Postdoctoral Education Audience Information For: Prospective Students New Students Current Students Faculty & Staff Main About Tech Program Information Cost & Funding Admissions Professional Development News & Events Quick Actions Apply FAQs Request Info Give ",
    "=\"https://www.googletagmanager.com/ns.html?id=GTM-5LK9HJ2\" =\"0\" =\"0\" =\"display:;:",
    "sk_",
    "DIVISION OF GRADUATE EDUCATION AND POSTDOCTORAL AFFAIRS UC San Diego ",
]

# ============================================================
# 設定要掃描的資料夾路徑（預設為目前目錄）
# Set the folder path to scan (default: current directory)
# ============================================================
SCAN_FOLDER = Path(__file__).resolve().parent / "data"


def remove_keywords(text: str, keywords: list[str]) -> str:
    """Remove all occurrences of each keyword from text."""
    for kw in keywords:
        # Use re.escape to handle special characters safely
        pattern = re.escape(kw)
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    # Clean up extra whitespace left behind
    text = re.sub(r"\s{3,}", "  ", text)
    return text.strip()


def process_file(filepath: str) -> None:
    """Read a _data.json file, clean the data field, and save it back."""
    print(f"Processing: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not isinstance(records, list):
        print(f"  ⚠️  Skipped — expected a list at top level, got {type(records).__name__}")
        return

    changed = 0
    for i, record in enumerate(records):
        if "data" in record and isinstance(record["data"], str):
            original = record["data"]
            cleaned = remove_keywords(original, KEYWORDS_TO_REMOVE)
            if cleaned != original:
                record["data"] = cleaned
                changed += 1

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"  ✅  Done — {changed} record(s) modified.")


def clean():
    pattern = os.path.join(SCAN_FOLDER, "**", "*_data.json")
    files = glob.glob(pattern, recursive=True)

    if not files:
        print(f"No *_data.json files found under: {os.path.abspath(SCAN_FOLDER)}")
        return

    print(f"Found {len(files)} file(s) to process.\n")
    for fp in files:
        process_file(fp)
    print("\nAll done!")


if __name__ == "__main__":
    clean()