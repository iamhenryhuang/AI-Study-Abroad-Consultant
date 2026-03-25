# 設定路徑抓取的黑名單
IGNORED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".mp3", ".avi", ".mov",
    ".css", ".js", ".json", ".xml",
}

BLACKLIST_PATH_FRAGMENTS = [

    # === 認證 / 登入 / SSO ===
    "shibboleth", "log-out", "logout", "/auth/", "/sso/",

    # === 個人化 / 推薦 / 收藏 ===
    "smartrec", "my-favorites", "favorites","building","about","oar2",

    # === 捐款 / 校友 / 基金 ===
    "giving", "alumni", "endowment","history"

    # === 新聞 / 部落格 / 活動頁 ===
    "/news/", "/events/", "/blog/", "news", "blog",

    # === 校園活動 / 儀式 ===
    "hooding-ceremony", "commencement", "grad-slam",

    # === 學籍 / 學術流程 ===
    "leave-of-absence", "withdrawal", "dissertation",
    "doctoral-committee", "thesis-committee",
    "file-your-thesis", "advancement-to-candidacy",

    # === 人資 / 工作 / 教職 ===
    "working-at", "postdoc", "for-faculty",
    "human-resources", "payroll", "Faculty", "staff",

    # === 教育階段（非研究所） ===
    "undergraduate", "/ugrad/", "high-school", "k-12",

    # === 網站系統 / 法務 ===
    "sitemap", "privacy-policy", "terms-of-use", "website-feedback",

    # === 學院 / 領域（非目標） ===
    "music", "humanities", "architecture", "nursing",
    "public-affairs", "health", "social", "biology", "medicine",
    "law", "school-of-theater-film-and-television",
    "anderson", "david-geffen-school-of-medicine",
    "international-institute", "physical-sciences",
    "chemistry-and-biochemistry-department",
    "school-of-education-and-information-studies",
    "civil-environmental", "dentistry","accounting",
    "business","civil","geography","environmental","food","microbiology","chemistry","astronomy",
    "animal","biotechnology","arts","cognitive","chemical",
    "anthropology","comparative-literature",

    # === 人物 / 社群 / 生活 ===
    "people", "family", "life","faculty",

    # === 榮譽 / 亮點 / 宣傳 ===
    "awards", "spotlight", "spotlights", "insights",

    # === 活動 / 研討會 / 課程 ===
    "seminar", "seminars", "workshop", "workshops", "webinar",
    "conference", "sponsorship",

    # === AI / 技術主題 ===
    "agentic-ai",

    # === 商業 / 行銷 / 內容 ===
    "shirt", "design", "competition", "generating",

    # === 網站結構 / 分類 ===
    "taxonomy", "directory","award",

    # 年分
    "2005","2006","2007","2008","2009", 
    "2010","2011","2012","2013","2014","2015","2016","2017","2018","2019","2020",

    # ucsd
    "doctoral","caltech.edu/academics/courses/","past-course-offerings","canvas.ucsd.edu","easy.ucsd.edu",
    
    # 

]

