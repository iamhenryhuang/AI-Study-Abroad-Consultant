import unittest

from playwright.sync_api import sync_playwright

from data_crawler.browser import _CLICK_EXPANDERS_JS, _FORCE_SHOW_JS
from data_crawler.url_tools import (
    application_scope_exclusion,
    get_root_info,
    is_high_value_sibling_url,
    is_same_host,
    is_same_root,
)
from data_crawler.nodes_page import (
    TARGET_PROGRAM_CODE,
    _deadline_semantics_supported,
    _excerpt_context,
    _field_semantics_supported,
    _issues_as_evidence,
    _normalize_target_codes,
    _promote_grounded_evidence,
    identify_programs,
)
from data_crawler.nodes_school import _crawl_candidate_priority, _program_write_priority


class DataCrawlerBrowserTest(unittest.TestCase):
    def test_expander_script_does_not_click_navigation_links(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(
                """
                <a id="nav" href="https://example.com/other" aria-expanded="false">Menu</a>
                <button id="expand" aria-expanded="false"
                        onclick="this.dataset.clicked='yes'">Expand</button>
                """
            )

            page.evaluate(_CLICK_EXPANDERS_JS)

            self.assertEqual(page.url, "about:blank")
            self.assertEqual(
                page.locator("#nav").get_attribute("href"),
                "https://example.com/other",
            )
            self.assertEqual(
                page.locator("#expand").get_attribute("data-clicked"),
                "yes",
            )
            browser.close()

    def test_force_show_reveals_controlled_panel_but_not_hidden_navigation(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(
                """
                <button aria-controls="answer">Question</button>
                <section id="answer" hidden>Admission answer</section>
                <nav><div id="mobile-menu" hidden>Unrelated menu</div></nav>
                """
            )

            page.evaluate(_FORCE_SHOW_JS)

            self.assertTrue(page.locator("#answer").is_visible())
            self.assertFalse(page.locator("#mobile-menu").is_visible())
            browser.close()

    def test_high_value_same_host_sibling_is_allowed_without_opening_whole_site(self):
        root = get_root_info("https://grad.example.edu/admissions/")
        tuition = "https://grad.example.edu/financial/tuition-fees.html"
        news = "https://grad.example.edu/news/faculty-award.html"

        self.assertFalse(is_same_root(tuition, root))
        self.assertTrue(is_same_host(tuition, root))
        self.assertTrue(is_high_value_sibling_url(tuition, "Tuition & Fees"))
        self.assertFalse(is_high_value_sibling_url(news, "Faculty Award"))
        self.assertFalse(is_high_value_sibling_url(
            "https://grad.example.edu/admitted-students/conditions-admission",
            "Conditions of Admission",
        ))

    def test_current_student_fellowship_index_is_out_of_admission_scope(self):
        self.assertIsNotNone(application_scope_exclusion(
            "https://grad.example.edu/funding/current-fellowships",
            "New Fellowships",
        ))

    def test_ta_oral_english_page_is_out_of_admission_scope(self):
        self.assertIsNotNone(application_scope_exclusion(
            "https://cs.example.edu/graduate/financial_support/english-proficiency.html",
            "Oral English Proficiency",
        ))

    def test_school_wide_page_targets_international_cs_masters(self):
        result = identify_programs({
            "page": {
                "url": "https://grad.example.edu/admissions/english-proficiency",
                "title": "English Proficiency Requirements",
                "full_text": "International master's applicants must submit an English test.",
                "h1_list": ["English Proficiency Requirements"],
            },
            "classification": {"types": [{"type": "admissions", "confidence": 1.0}]},
        })

        self.assertEqual(result["program_codes"][0]["program_code"], TARGET_PROGRAM_CODE)
        self.assertEqual(result["scope"], "school_wide")

    def test_cs_department_page_targets_same_record_without_heading_gate(self):
        result = identify_programs({
            "page": {
                "url": "https://cse.example.edu/graduate/admissions",
                "title": "Graduate Admissions",
                "full_text": "The MS application requires three recommendation letters.",
                "h1_list": ["Graduate Admissions"],
            },
        })

        self.assertEqual(result["program_codes"][0]["program_code"], TARGET_PROGRAM_CODE)
        self.assertIn(result["scope"], ("department_wide", "program_specific"))

    def test_phd_only_school_does_not_create_fake_masters_target(self):
        result = identify_programs({
            "page": {
                "url": "https://eecs.example.edu/graduate/admission",
                "title": "EECS Admission Process",
                "full_text": (
                    "Application is for the doctoral program only. "
                    "The department does not offer a terminal master's degree."
                ),
                "h1_list": ["EECS Admission Process"],
            },
        })

        self.assertEqual(result["program_codes"], [])
        self.assertFalse(result["target_assessment"]["masters_available"])
        self.assertEqual(result["scope"], "masters_unavailable")

    def test_generic_grad_page_with_postdoctoral_nav_is_not_phd_only(self):
        result = identify_programs({
            "page": {
                "url": "https://grad.example.edu/admissions",
                "title": "Apply Now",
                "full_text": (
                    "Graduate degree programs welcome international applicants. "
                    "Admissions Financial Support Postdoctoral Affairs."
                ),
                "h1_list": ["Admissions"],
            },
        })

        self.assertEqual(result["program_codes"][0]["program_code"], TARGET_PROGRAM_CODE)
        self.assertNotEqual(result["scope"], "masters_unavailable")

    def test_core_requirement_urls_precede_joint_degree_urls(self):
        candidates = [
            {"url": "https://cs.example.edu/admissions/joint-degree-msmba", "root_index": 0},
            {"url": "https://grad.example.edu/english-proficiency", "root_index": 1},
            {"url": "https://cs.example.edu/application-checklist", "root_index": 0},
        ]
        ordered = sorted(candidates, key=_crawl_candidate_priority)

        self.assertIn("application-checklist", ordered[0]["url"])
        self.assertIn("english-proficiency", ordered[1]["url"])
        self.assertIn("joint-degree", ordered[2]["url"])

    def test_test_score_row_uses_nearby_table_header_for_semantics(self):
        source = (
            "English language tests. IELTS Academic applicants must meet this standard. "
            "Minimum required score: 7.0 (Academic) with at least 6.5 for each component."
        )
        excerpt = "Minimum required score: 7.0 (Academic) with at least 6.5 for each component."
        context = _excerpt_context(excerpt, source)

        self.assertTrue(_field_semantics_supported("ielts_min", context))

    def test_updated_toefl_date_header_supports_new_scale(self):
        context = (
            "TOEFL taken on or after January 21, 2026. "
            "Minimum Score for Admission Consideration: 4.5"
        )
        self.assertTrue(_field_semantics_supported("toefl_ibt_new_scale_min", context))

    def test_character_limit_is_not_a_word_limit(self):
        self.assertFalse(_field_semantics_supported(
            "sop_word_limit",
            "The statement of purpose must be no more than 8,000 characters.",
        ))

    def test_grounded_recommendation_count_and_full_deadline_are_promoted(self):
        extraction = {
            "programs": [],
            "deadlines": [],
            "evidence_paragraphs": [
                {
                    "program_code": TARGET_PROGRAM_CODE,
                    "category": "materials",
                    "field_name": "rec_letter_count",
                    "evidence_text": "At least three letters of recommendation are required.",
                    "source_excerpt": "At least three letters of recommendation must be submitted.",
                },
                {
                    "program_code": TARGET_PROGRAM_CODE,
                    "category": "deadline",
                    "field_name": "general",
                    "evidence_text": "Fall 2026 start—December 1, 2025 application deadline.",
                    "source_excerpt": "Fall 2026 start—December 1, 2025",
                },
            ],
        }

        promoted = _promote_grounded_evidence(extraction)

        self.assertEqual(
            promoted["programs"][0]["fields"]["rec_letter_count"]["value"], 3
        )
        self.assertEqual(
            promoted["deadlines"][0]["application_close_date"], "2025-12-01"
        )
        self.assertEqual(promoted["deadlines"][0]["semester"], "fall_2026")

    def test_test_validity_date_is_not_promoted_as_application_deadline(self):
        extraction = {
            "programs": [],
            "deadlines": [],
            "evidence_paragraphs": [{
                "program_code": TARGET_PROGRAM_CODE,
                "category": "deadline",
                "field_name": "general",
                "evidence_text": (
                    "Application Open Date September 2026; "
                    "Earliest Valid Test Date September 1, 2021."
                ),
                "source_excerpt": (
                    "Application Open Date September 2026 | "
                    "Earliest Valid Test Date September 1, 2021"
                ),
            }],
        }

        promoted = _promote_grounded_evidence(extraction)

        self.assertEqual(promoted["deadlines"], [])

    def test_program_specific_page_is_written_after_school_wide_page(self):
        results = [
            {"scope": "program_specific", "url": "https://cs.example.edu/ms-requirements"},
            {"scope": "school_wide", "url": "https://grad.example.edu/english"},
            {"scope": "department_wide", "url": "https://cs.example.edu/admissions"},
        ]
        ordered = sorted(results, key=_program_write_priority)

        self.assertEqual(
            [item["scope"] for item in ordered],
            ["school_wide", "department_wide", "program_specific"],
        )

    def test_non_program_and_empty_deadlines_are_rejected(self):
        fee_waiver = {
            "deadline_type": "regular",
            "application_close_date": "2026-01-10",
        }
        empty = {"deadline_type": "regular"}
        application = {
            "deadline_type": "regular",
            "application_close_date": "2026-12-15",
        }

        self.assertFalse(_deadline_semantics_supported(
            fee_waiver, "Fee waiver request deadline is January 10, 2026."
        ))
        self.assertFalse(_deadline_semantics_supported(
            empty, "The application deadline is February 1."
        ))
        self.assertTrue(_deadline_semantics_supported(
            application, "The application deadline is December 15, 2026."
        ))

    def test_rejected_grounded_value_becomes_rag_evidence_paragraph(self):
        excerpt = "The application deadline is February 1 for the following fall semester."
        issues = [{
            "program_code": TARGET_PROGRAM_CODE,
            "field_name": "deadlines[regular]",
            "field_value": None,
            "source_excerpt": excerpt,
            "problem": "not an actionable program application deadline",
        }]
        page = {
            "full_text": (
                "MS Computer Science admissions. "
                f"{excerpt} International applicants use the graduate application."
            )
        }

        evidence = _issues_as_evidence(issues, page)

        self.assertEqual(evidence[0]["category"], "deadline")
        self.assertEqual(evidence[0]["evidence_kind"], "validator_rejected")
        self.assertIn("international applicants", evidence[0]["evidence_text"])
        self.assertEqual(evidence[0]["source_excerpt"], excerpt)

    def test_llm_program_codes_are_forced_to_single_target(self):
        extraction = {
            "programs": [{"program_code": "CS MS", "fields": {}}],
            "deadlines": [{"program_code": "CS PhD"}],
            "scholarships": [{"program_code": "SCHOOL_WIDE"}],
            "app_materials": [{"program_code": "made-up-code"}],
        }

        normalized = _normalize_target_codes(extraction, [TARGET_PROGRAM_CODE])

        self.assertEqual(normalized["programs"][0]["program_code"], TARGET_PROGRAM_CODE)
        self.assertEqual(normalized["deadlines"][0]["program_code"], TARGET_PROGRAM_CODE)
        self.assertEqual(normalized["scholarships"][0]["program_code"], TARGET_PROGRAM_CODE)
        self.assertEqual(normalized["app_materials"][0]["program_code"], TARGET_PROGRAM_CODE)


if __name__ == "__main__":
    unittest.main()
