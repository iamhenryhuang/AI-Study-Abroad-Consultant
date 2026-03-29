from dataclasses import dataclass
# url與data爬蟲的參數

# ===================== #
# url crawler parameter #
# ===================== #
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

@dataclass(frozen=True)
class CrawlConfig:
    MAX_DEPTH: int = 5
    CRAWL_DELAY_MS: int = 400
    RENDER_WAIT_MS: int = 800
    STRIP_QUERY: bool = True
    MAX_PAGES: int = 800
    NUM_WORKERS: int = 5
CONFIG = CrawlConfig()


# ==================== #
# page score parameter #
# ==================== #
THRESHOLDS = {
    "admissions": 24,
    "program":    15,
    "tuition":    10,
    "faq":        13,
}

MAX_FULL_TEXT_CHARS = 600_000
MAX_RETRY = 3
RETRY_WAIT_MS = 2000

KEYWORD_IN_CONTENT = 1
H3_WEIGHT_H3    = 2.5
H2_WEIGHT_H2    = 3.5
H1_WEIGHT_H1    = 4.5

KEYWORD_MINUS = 1
H3_WEIGHT_MINUS = 3
H2_WEIGHT_MINUS = 4
H1_WEIGHT_MINUS = 5

EMPHASIS_MAX_BONUS = 2

DENSITY_MIN_COUNT = 4
DENSITY_BONUS     = 1
DENSITY_MAX_BONUS = 2

NAV_WEIGHT = 0.3

# page score keyword list
PAGE_HINTS = {
    "admissions": [
        "admission", "admissions", "apply", "application",
        "deadline", "deadlines", "requirement", "requirements",
        "eligibility", "how to apply", "gre", "toefl", "ielts",
        # BUG FIX: 原本 "enrollment" 後缺逗號，與下一行 "Criteria" 拼接成 "enrollmentCriteria"
        "prerequisites", "prerequisite", "prepare", "enroll", "enrollment",
        "criteria", "prospective", "applicants", "timetable",
        # BUG FIX: 移除空字串 "" (會使每個頁面都多得 +1 分)
        # BUG FIX: 移除重複的 "international" 與 "ready-to-apply"
        "international", "applicant",
        "ready-to-apply", "before-you-apply", "fellowships", "tips-for-applying",
        "step", "checklist", "gpa", "score", "official transcript",
        "letter of recommendation", "statement of purpose",
    ],
    "program": [
        "master of science", "ms in computer science",
        "program overview", "curriculum", "degree requirements",
        "course", "program", "degree", "applicants",
    ],
    "tuition": [
        "tuition", "fees", "cost", "costs", "scholarship",
        "financial aid", "financial",
    ],
    "faq": [
        "frequently asked questions", "faqs", "fellowship",
        "questions", "question", "apply",
    ],
}

MINUS_KEYWORDS = [

    # === 新聞 / 活動 / 故事內容 ===
    "news", "events", "stories",

    # === 人員 / 校園角色 ===
    "faculty", "staff",

    # === 校園生活 / 社群 ===
    # BUG FIX: 移除重複的 "life"
    "life", "club",

    # === 校友 ===
    "alumni",

    # NOTE: "funding" 與 "research" 從此移除
    # 原因: "financial aid / funding" 是申請資訊的一部分（WashU、Stanford 有 funding 頁）
    #       "research" 出現在 "research interests"、"research experience" 等申請相關描述中
    #       誤扣分會造成有效的申請頁面被過濾掉

    # === 教育階段 ===
    "undergraduate", "undergraduates",

    # === 榮譽 / 獎項 ===
    "awards", "award",

    # === 設施 / 校園資源 ===
    # BUG FIX: 原本 "Just a moment" 後缺逗號，與 "music" 拼接成 "Just a momentmusic"
    # 導致 music/humanities/arts/architecture 等整排扣分字完全失效
    "facilities", "facility",

    # === 學院 / 領域（非目標） ===
    "music", "humanities", "arts", "art", "architecture", "nursing",
    "public-affairs", "health", "social", "biology", "medicine",
    "law",
]

URL_PATH_HINTS = {
    "admissions": [
        ("admissions", 6), ("admission", 3.5), ("apply", 5),
        ("application", 4), ("how-to-apply", 5), ("how_to_apply", 5),
        ("checklist", 4), ("requirements", 5), ("eligibility", 10),
        ("program", 3), ("undergradute", -5), ("phd", -3), ("prospective", 3),
        ("english-language-proficiency", 8), ("proficiency", 4), ("PhD", -3),
        ("international-applicants", 8), ("guidelines", 4), ("international", 8),
        ("check", 5), ("ready-to-apply", 6), ("Page Not Found", -10), ("fellowships", 4),
        ("tips-for-applying", 5), ("international/ready-to-apply", 10),
        ("international/before-to-apply", 10), ("domestic/before-to-apply", 10),
        ("/domestic/ready-you-apply", 10), ("step", 4), ("process", 6),
        ("timelines", 8), ("english-proficiency", 8), ("gpa", 15), ("gre", 10),
    ],
    "program": [
        ("grad_cs", 5), ("graduate", 5), ("ms-program", 5),
        ("ms_program", 5), ("degree", 8), ("curriculum", 4),
        ("degree-programs", 5), ("degree_programs", 5), ("undergradute", -5),
        ("program", 4), ("computer science", 3), ("Page Not Found", -10),
    ],
    "tuition": [
        ("tuition", 10), ("financial", 10), ("financial-assistance", 5),
        ("financial_assistance", 5), ("cost", 10), ("fees", 10),
        ("scholarship", 15), ("fellowships", 15), ("funding", 4),
        ("reimbursement", 6), ("tax", 5), ("Page Not Found", -10),
    ],
    "faq": [
        ("faq", 8), ("faqs", 8), ("faq-applicants", 6),
        ("frequently-asked", 7), ("frequently_asked", 6),
        ("Page Not Found", -10),
    ],
}
