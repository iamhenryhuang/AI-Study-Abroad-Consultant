import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "backend" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from retriever.sql_search import (  # noqa: E402
    SCHEMA_DESCRIPTION,
    _ALLOWED_TABLES,
    _build_sql_prompt,
    _is_sql_safe,
)


class SQLSearchSchemaTest(unittest.TestCase):
    def test_current_single_program_code_is_described(self):
        prompt = _build_sql_prompt("UCLA CS 碩士的英文要求")

        self.assertIn("INTERNATIONAL_CS_MASTERS", prompt)
        self.assertNotIn("program_code = 'CS MS'", prompt)

    def test_program_evidence_is_allowed_and_documented(self):
        self.assertIn("program_evidence", _ALLOWED_TABLES)
        self.assertIn("evidence_text", SCHEMA_DESCRIPTION)
        self.assertTrue(_is_sql_safe(
            "SELECT pe.evidence_text "
            "FROM program_evidence pe "
            "JOIN programs p ON p.id = pe.program_id "
            "JOIN universities u ON u.id = p.university_id"
        ))

    def test_unrelated_write_queries_remain_forbidden(self):
        self.assertFalse(_is_sql_safe("UPDATE programs SET gpa_min = 4.0"))


if __name__ == "__main__":
    unittest.main()
