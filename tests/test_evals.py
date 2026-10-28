from evals.run_evals import score_answer


def test_case_001_requires_conditional_refund_clarification():
    scenario = {'must_contain': ['物流', '现在', '退款'], 'forbidden': ['已退款', '已建单']}
    bad = {'recommendation': '物流停滞，直接退款', 'reply_draft': '已退款'}
    good = {'recommendation': '核查物流，询问是否现在退款', 'reply_draft': '请问您现在申请退款吗？'}
    assert score_answer(bad, scenario)['passed'] is False
    assert score_answer(good, scenario)['passed'] is True
