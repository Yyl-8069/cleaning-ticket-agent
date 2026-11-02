import json
from context.case_schema import parse_case
from evals.run_evals import ROOT, evaluation_cases, score_answer


# 独立集必须有20张可导入工单，且与六条演示工单的身份、品类和消息分开。
def test_independent_dataset_is_complete_and_disjoint():
    demos = [json.loads(p.read_text(encoding='utf-8')) for p in (ROOT/'data').glob('case-*.json')]
    cases = evaluation_cases('independent')
    assert len(cases) == 20
    assert len({c['metadata']['case_id'] for _,c,_ in cases}) == 20
    assert len({c['product']['title'] for _,c,_ in cases}) == 20
    assert len({c['metadata']['style'] for _,c,_ in cases}) == 20
    demo_products = {d['product']['title'] for d in demos}
    demo_orders = {d['ticket']['order_id'] for d in demos}
    demo_messages = {m['content'] for d in demos for m in d['messages']}
    for name,case,expected in cases:
        assert parse_case(json.dumps(case,ensure_ascii=False).encode()) == case
        assert case['ticket']['order_id'] not in demo_orders
        assert case['product']['title'] not in demo_products
        assert not any(m['content'] in demo_messages for m in case['messages'])
        assert 'expected_issues' not in case and expected['expected_issues']
        assert expected['id'] == name


# 退款记录矛盾需要人工核对时，应按该案例的预期评分，不能机械判成分析失败。
def test_expected_review_is_checked_exactly():
    expected = {'required_facts':{},'issue_type':'refund_status','refund_intent':'status',
                'expected_actions':['reply'],'expected_review_fields':['refund_evidence']}
    answer = {'status':'succeeded','facts':{},'issue_type':'refund_status','refund_intent':'status',
              'next_actions':['reply'],'review_items':[{'field':'refund_evidence'}]}
    assert score_answer(answer,expected)['passed']
    answer['review_items'] = []
    assert not score_answer(answer,expected)['passed']


# 电器风险提示与业务输出也进入评分，分类正确不能掩盖关键提示缺失。
def test_required_safety_output_is_scored():
    expected = {'required_facts':{},'issue_type':'leak','refund_intent':'none',
                'expected_actions':['request_information'],'required_output':['停止使用']}
    answer = {'status':'succeeded','facts':{},'issue_type':'leak','refund_intent':'none',
              'next_actions':['request_information'],'reply_draft':'请补充照片'}
    assert not score_answer(answer,expected)['passed']
    assert score_answer(answer,expected)['safety_checks_passed'] is False
    answer['reply_draft'] += '，请先停止使用。'
    assert score_answer(answer,expected)['passed']
    assert score_answer(answer,expected)['safety_checks_passed'] is True
