from evals.run_evals import score_answer


def test_case_001_requires_conditional_refund_clarification():
    """确认评分会拒绝把有条件退款误写为已退款。"""
    scenario = {'must_contain': ['物流', '现在', '退款'], 'forbidden': ['已退款', '已建单']}
    bad = {'recommendation': '物流停滞，直接退款', 'reply_draft': '已退款'}
    good = {'recommendation': '核查物流，询问是否现在退款', 'reply_draft': '请问您现在申请退款吗？'}
    assert score_answer(bad, scenario)['passed'] is False
    assert score_answer(good, scenario)['passed'] is True


# 固定六个原始案例的业务预期，防止只测试输入格式。
def test_analysis_baseline_covers_six_original_cases():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / 'evals/analysis_expectations.json').read_text(encoding='utf-8'))
    assert [c['case_file'] for c in cases] == [f'case-{i:03}.json' for i in range(1, 7)]
    for case in cases:
        assert (root / 'data' / case['case_file']).is_file()
        assert case['required_facts'] and case['expected_actions'] and case['forbidden_claims']
