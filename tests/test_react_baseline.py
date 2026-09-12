"""ReAct baseline（對照組）單元測試。

只驗證「工具包裝把參數正確傳給現成檢索函式」與「手刻 ReAct 迴圈的調度/收尾邏輯」，
不呼叫真的 LLM、不連 DB——底層檢索函式與 call_llm 全部 mock 掉。

對照設計備忘（給審稿/日後的人）：
  - 工具集刻意與現有 LangGraph agent 對齊（6 個），差別只在「調度方式」。
  - 工具不做學校偵測：school_id 由 LLM 從問題自行推斷後傳入（見 tools.py）。
  - 無 Verifier / Critic 護欄——這是「純 ReAct」對照組的定義。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "scripts"))

from retriever.react_baseline import tools as react_tools
from retriever.react_baseline import react_agent


# ─── 工具包裝：驗證參數正確傳給現成函式、回傳正規化為 list[dict] ────────────────

class TestToolWrappers(unittest.TestCase):
    def test_registry_has_six_aligned_tools(self):
        """工具集必須是與現有 agent 對齊的那 6 個，不多不少。"""
        self.assertEqual(
            set(react_tools.TOOLS),
            {"sql_search", "fulltext_search", "applicant_search",
             "professor_list_search", "fetch_professor", "recommend"},
        )
        for name, spec in react_tools.TOOLS.items():
            self.assertIn("fn", spec, f"{name} 缺 fn")
            self.assertTrue(spec.get("description"), f"{name} 缺工具說明")

    def test_sql_search_tool_unwraps_tuple(self):
        """sql_search 現成函式回 (rows, sql) tuple；工具要取 rows 回傳 list[dict]。"""
        with patch.object(react_tools, "sql_search",
                          return_value=([{"school_id": "mit"}], "SELECT 1")) as m:
            out = react_tools.TOOLS["sql_search"]["fn"](query="MIT TOEFL")
        m.assert_called_once_with("MIT TOEFL")
        self.assertEqual(out, [{"school_id": "mit"}])

    def test_fulltext_tool_passes_school_id_from_llm(self):
        """school_id 由 LLM 傳入，工具原封轉給 hybrid_search_with_fallback（不自行偵測）。"""
        with patch.object(react_tools, "hybrid_search_with_fallback",
                          return_value=[{"chunk_text": "x"}]) as m:
            out = react_tools.TOOLS["fulltext_search"]["fn"](
                query="credits", school_id="cmu")
        m.assert_called_once_with("credits", school_id="cmu")
        self.assertEqual(out, [{"chunk_text": "x"}])

    def test_fulltext_tool_school_id_optional(self):
        """LLM 沒推斷出學校時 school_id 省略，應以 None 傳入。"""
        with patch.object(react_tools, "hybrid_search_with_fallback",
                          return_value=[]) as m:
            react_tools.TOOLS["fulltext_search"]["fn"](query="general")
        m.assert_called_once_with("general", school_id=None)

    def test_applicant_search_tool(self):
        with patch.object(react_tools, "applicant_search",
                          return_value=[{"gpa": 3.8}]) as m:
            out = react_tools.TOOLS["applicant_search"]["fn"](
                query="chance", school_id="stanford")
        m.assert_called_once_with("chance", school_id="stanford")
        self.assertEqual(out, [{"gpa": 3.8}])

    def test_professor_list_tool_requires_school(self):
        with patch.object(react_tools, "professor_list_search",
                          return_value=[{"name": "Ng"}]) as m:
            out = react_tools.TOOLS["professor_list_search"]["fn"](school_id="stanford")
        m.assert_called_once_with("stanford")
        self.assertEqual(out, [{"name": "Ng"}])

    def test_fetch_professor_tool_builds_query_dict(self):
        """LLM 給 name/school/school_id，工具組成 run_professor_fetch 要的 dict。"""
        with patch.object(react_tools, "run_professor_fetch",
                          return_value=[{"chunk_text": "papers"}]) as m:
            out = react_tools.TOOLS["fetch_professor"]["fn"](
                name="Andrew Ng", school="Stanford", school_id="stanford",
                query="Ng research")
        m.assert_called_once_with(
            {"name": "Andrew Ng", "school": "Stanford", "school_id": "stanford"},
            "Ng research",
        )
        self.assertEqual(out, [{"chunk_text": "papers"}])

    def test_recommend_tool_flattens_tiers_to_docs(self):
        """recommend 現成函式回 tier 分組 dict；工具攤平成 list[dict] 與其他工具一致。"""
        fake_tiers = {
            "衝刺": [{"school_id": "mit", "name": "MIT"}],
            "適中": [],
            "保底": [{"school_id": "uci", "name": "UCI"}],
        }
        with patch.object(react_tools, "recommend", return_value=fake_tiers) as m:
            out = react_tools.TOOLS["recommend"]["fn"](
                profile={"gpa": 3.5, "toefl": 100})
        m.assert_called_once_with({"gpa": 3.5, "toefl": 100})
        self.assertIsInstance(out, list)
        sids = {d["school_id"] for d in out}
        self.assertEqual(sids, {"mit", "uci"})
        # tier 要標在攤平後的每筆上，否則資訊遺失
        for d in out:
            self.assertIn(d["tier"], ("衝刺", "適中", "保底"))


# ─── ReAct 迴圈：驗證解析 action、派工、收尾、步數上限 ─────────────────────────

def _llm_script(*responses):
    """回傳一個假的 call_llm：依序吐出預先寫好的 LLM 回應。"""
    it = iter(responses)

    def fake(prompt, *args, **kwargs):
        return next(it)

    return fake


class TestReactLoop(unittest.TestCase):
    def test_immediate_final_answer(self):
        """LLM 第一步就給 final_answer → 不呼叫任何工具，直接收尾。"""
        script = _llm_script(json.dumps({"final_answer": "MIT 需要 TOEFL 90"}))
        with patch.object(react_agent, "call_llm", side_effect=script):
            result = react_agent.run_react("MIT TOEFL?")
        self.assertEqual(result["answer"], "MIT 需要 TOEFL 90")
        self.assertEqual(result["trace"], [])

    def test_one_tool_then_answer(self):
        """一步工具 → 觀察回饋 → 第二步收尾。驗證 trace 記錄該次工具呼叫。"""
        script = _llm_script(
            json.dumps({"tool": "sql_search", "args": {"query": "MIT TOEFL"}}),
            json.dumps({"final_answer": "TOEFL 90"}),
        )
        fake_tool = lambda query: [{"toefl_min": 90}]
        with patch.object(react_agent, "call_llm", side_effect=script), \
             patch.dict(react_tools.TOOLS,
                        {"sql_search": {"fn": fake_tool, "description": "d"}},
                        clear=False):
            result = react_agent.run_react("MIT TOEFL?")
        self.assertEqual(result["answer"], "TOEFL 90")
        self.assertEqual(len(result["trace"]), 1)
        self.assertEqual(result["trace"][0]["tool"], "sql_search")
        self.assertEqual(result["trace"][0]["args"], {"query": "MIT TOEFL"})

    def test_unknown_tool_feeds_error_not_crash(self):
        """LLM 叫了不存在的工具 → 不當機，把錯誤當 observation 回饋，續跑到收尾。"""
        script = _llm_script(
            json.dumps({"tool": "no_such_tool", "args": {}}),
            json.dumps({"final_answer": "done"}),
        )
        with patch.object(react_agent, "call_llm", side_effect=script):
            result = react_agent.run_react("q")
        self.assertEqual(result["answer"], "done")
        self.assertEqual(len(result["trace"]), 1)
        self.assertIn("error", result["trace"][0]["result_preview"].lower())

    def test_step_cap_stops_runaway_loop(self):
        """LLM 永遠只叫工具、不收尾 → 到 max_steps 必須停，回傳目前狀態不無限迴圈。"""
        always_tool = json.dumps({"tool": "sql_search", "args": {"query": "x"}})
        script = _llm_script(*([always_tool] * 50))
        fake_tool = lambda query: []
        with patch.object(react_agent, "call_llm", side_effect=script), \
             patch.dict(react_tools.TOOLS,
                        {"sql_search": {"fn": fake_tool, "description": "d"}},
                        clear=False):
            result = react_agent.run_react("q", max_steps=3)
        # 撞上限：工具最多被叫 max_steps 次，且仍回傳一個（可能不完整的）答案字串
        self.assertLessEqual(len(result["trace"]), 3)
        self.assertIsInstance(result["answer"], str)


class TestReactWithCritic(unittest.TestCase):
    """第三組 ReAct + Critic：enable_critic 旗標。"""

    def test_default_no_critic_unchanged(self):
        """enable_critic 預設 False → 純 ReAct 行為不變，且標記 critic 未作動。"""
        script = _llm_script(json.dumps({"final_answer": "TOEFL 90"}))
        with patch.object(react_agent, "call_llm", side_effect=script):
            result = react_agent.run_react("MIT TOEFL?")
        self.assertEqual(result["answer"], "TOEFL 90")
        self.assertFalse(result["critic_applied"])
        self.assertFalse(result["critic_flagged"])

    def test_collected_docs_accumulates_full_results(self):
        """迴圈要另存每步工具的『完整 docs』（非截斷），供 Critic 使用。"""
        full_docs = [{"chunk_text": "x" * 800, "source_url": "u", "school_id": "mit"}]
        script = _llm_script(
            json.dumps({"tool": "sql_search", "args": {"query": "q"}}),
            json.dumps({"final_answer": "ans"}),
        )
        with patch.object(react_agent, "call_llm", side_effect=script), \
             patch.dict(react_tools.TOOLS,
                        {"sql_search": {"fn": lambda query: full_docs, "description": "d"}},
                        clear=False):
            docs = react_agent._collect_docs_for_critic("MIT?", max_steps=6)
        # 完整 docs 應保留（不像 trace preview 被截斷到 500）
        self.assertEqual(docs, full_docs)

    def test_critic_flags_hallucination_appends_warning(self):
        """enable_critic=True 且 Critic 判 has_issue → 答案尾巴附上 ⚠️ 警告。

        ReAct 先查一次工具（有 docs，Critic 才會作動，與 agent critic_node 語意一致），
        再收尾；接著 Critic 判斷。
        """
        script = _llm_script(
            json.dumps({"tool": "applicant_search", "args": {"query": "MIT", "school_id": "mit"}}),
            json.dumps({"final_answer": "MIT TOEFL 是 102"}),
            json.dumps({"has_issue": True, "issue_summary": "102 這個數字在參考資料中找不到根據"}),
        )
        fake_tool = lambda query, school_id=None: [{"chunk_text": "某人 GPA 3.9 上 MIT"}]
        with patch.object(react_agent, "call_llm", side_effect=script), \
             patch.dict(react_tools.TOOLS,
                        {"applicant_search": {"fn": fake_tool, "description": "d"}},
                        clear=False):
            result = react_agent.run_react("MIT TOEFL?", enable_critic=True)
        self.assertTrue(result["critic_applied"])
        self.assertTrue(result["critic_flagged"])
        self.assertIn("⚠️", result["answer"])
        self.assertIn("MIT TOEFL 是 102", result["answer"])   # 原答案保留

    def test_critic_clean_answer_untouched(self):
        """Critic 判乾淨（has_issue=False）→ 答案不變、僅標記已檢查未觸發。"""
        script = _llm_script(
            json.dumps({"tool": "sql_search", "args": {"query": "MIT"}}),
            json.dumps({"final_answer": "系統未收錄 MIT 資料"}),
            json.dumps({"has_issue": False, "issue_summary": ""}),
        )
        fake_tool = lambda query: [{"chunk_text": "some doc"}]
        with patch.object(react_agent, "call_llm", side_effect=script), \
             patch.dict(react_tools.TOOLS,
                        {"sql_search": {"fn": fake_tool, "description": "d"}},
                        clear=False):
            result = react_agent.run_react("MIT TOEFL?", enable_critic=True)
        self.assertEqual(result["answer"], "系統未收錄 MIT 資料")
        self.assertTrue(result["critic_applied"])
        self.assertFalse(result["critic_flagged"])

    def test_critic_skipped_when_no_docs(self):
        """ReAct 沒查任何資料就作答 → 無 docs，Critic 跳過（與 agent critic_node 一致）。
        這也是一個要寫進論文的侷限：憑空作答時事後 Critic 無從檢查。"""
        script = _llm_script(json.dumps({"final_answer": "MIT TOEFL 大約 100"}))
        with patch.object(react_agent, "call_llm", side_effect=script):
            result = react_agent.run_react("MIT TOEFL?", enable_critic=True)
        self.assertTrue(result["critic_applied"])
        self.assertFalse(result["critic_flagged"])   # 無 docs → 沒觸發
        self.assertEqual(result["answer"], "MIT TOEFL 大約 100")


class TestCleanAnswer(unittest.TestCase):
    def test_plain_text_untouched(self):
        self.assertEqual(react_agent._clean_answer("MIT 需要 TOEFL 90"), "MIT 需要 TOEFL 90")

    def test_extracts_final_answer_from_json(self):
        self.assertEqual(
            react_agent._clean_answer('{"final_answer": "TOEFL 90"}'), "TOEFL 90")

    def test_extracts_thought_when_that_is_all_there_is(self):
        """撞上限時 LLM 回 {'thought': '...'} → 抽 thought，不殘留原始 JSON。"""
        self.assertEqual(
            react_agent._clean_answer('{"thought": "I was unable to find it."}'),
            "I was unable to find it.")


if __name__ == "__main__":
    unittest.main()
