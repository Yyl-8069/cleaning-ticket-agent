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


# 评分必须检查事实值，不能因出现正确关键词而放过错误金额。
def test_business_score_rejects_wrong_refund_amount():
    scenario = {'required_facts': {'refunded_amount_fen': 0}, 'issue_type': 'shipping_delay',
                'refund_intent': 'conditional', 'expected_actions': ['check_logistics']}
    bad = {'status': 'succeeded', 'facts': {'refunded_amount_fen': 5990},
           'issue_type': 'shipping_delay', 'refund_intent': 'conditional',
           'next_actions': ['check_logistics'], 'review_items': []}
    assert score_answer(bad, scenario)['passed'] is False


# 合理补证是有效分析，不因存在缺失信息就判业务失败。
def test_business_score_accepts_required_information_request():
    scenario = {'required_facts': {'refunded_amount_fen': 0}, 'issue_type': 'leak',
                'refund_intent': 'requested', 'expected_actions': ['request_information']}
    good = {'status': 'succeeded', 'facts': {'refunded_amount_fen': 0}, 'issue_type': 'leak',
            'refund_intent': 'requested', 'next_actions': ['request_information'],
            'missing_information': ['照片'], 'review_items': []}
    assert score_answer(good, scenario)['passed'] is True


# 包含正确动作还不够，额外错误退款审核也必须被评分拒绝。
def test_business_score_rejects_extra_forbidden_action():
    scenario = {'required_facts': {'refunded_amount_fen': 5990}, 'issue_type': 'refund_status',
                'refund_intent': 'status', 'expected_actions': ['reply'], 'forbidden_actions': ['human_approval']}
    answer = {'status': 'succeeded', 'facts': {'refunded_amount_fen': 5990}, 'issue_type': 'refund_status',
              'refund_intent': 'status', 'next_actions': ['reply', 'human_approval'], 'review_items': []}
    assert score_answer(answer, scenario)['passed'] is False
