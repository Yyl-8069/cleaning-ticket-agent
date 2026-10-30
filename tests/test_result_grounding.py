import json
from pathlib import Path
import pytest
from agents.ticket_agent import validate_result, AnalysisError
from app.storage import Storage
from tools.case_tools import CaseTools

ROOT = Path(__file__).resolve().parents[1]

# 建立隔离案例并查询实际证据，避免接触用户运行数据库。
def prepare(tmp_path, number=1, change=None):
    case = json.loads((ROOT / f'data/case-{number:03}.json').read_text(encoding='utf-8'))
    if change:
        change(case)
    storage = Storage(tmp_path / 'test.db')
    tools = CaseTools(storage, storage.save_case(case))
    ticket = case['ticket']
    tools.get_ticket(ticket['ticket_id'])
    tools.get_order(ticket['order_id'])
    tools.get_shipments(ticket['order_id'])
    tools.get_history(ticket['ticket_id'])
    tools.get_policy(ticket['shop_id'])
    tools.get_product(case['product']['product_id'])
    return tools

# 模型只提交语义判断和证据引用，不负责凭空生成业务事实。
def decision(issue='shipping_delay', actions=None, refund='conditional'):
    return {'issue_type': issue, 'refund_intent': refund,
            'next_actions': actions or ['check_logistics'],
            'evidence_refs': ['get_ticket', 'get_order', 'get_shipments', 'get_history', 'get_policy']}

# 含退款关键词的自由文本不参与事实校验，也不能直接进入页面。
@pytest.mark.parametrize('text', ['已退款金额为0', '尚未退款', '如果已退款请核对', '已经退款，private text'])
def test_prose_cannot_override_structured_evidence(tmp_path, text):
    tools = prepare(tmp_path)
    raw = decision()
    raw['reply_draft'] = text
    answer = validate_result(raw, tools)
    assert answer['status'] == 'succeeded'
    assert answer['review_items'] == []
    assert answer['facts']['refunded_amount_fen'] == 0
    assert '0.00' in answer['reply_draft']
    assert 'private text' not in json.dumps(answer)
    assert 'check_logistics' in answer['next_actions']

# 空查询产生有边界的缺失说明，不再导致事实来源校验失败。
def test_empty_policy_is_not_analysis_failure(tmp_path):
    tools = prepare(tmp_path, 4)
    assert tools.observed_results['get_policy']['status'] == 'not_found'
    answer = validate_result(decision('leak', ['request_information'], 'requested'), tools)
    assert answer['review_items'] == []
    assert 'check_policy' in answer['next_actions']
    assert '照片' in answer['reply_draft']
    assert any('规则' in item for item in answer['missing_information'])
    assert answer['facts']['order_status'] == 'delivered'

# 一致的订单金额与有效流水允许报告历史退款成功，但不承诺到账。
def test_confirmed_refund_is_reported_without_review(tmp_path):
    answer = validate_result(decision('refund_status', ['reply'], 'status'), prepare(tmp_path, 6))
    assert answer['review_items'] == []
    assert answer['facts']['refund_confirmed'] is True
    assert '59.90' in answer['reply_draft']
    assert '流水显示退款成功' in answer['reply_draft']
    assert '无法确认到账' in answer['reply_draft']
    assert 'request_information' not in answer['next_actions']

# 金额冲突或缺流水只影响退款结论，保留订单事实。
@pytest.mark.parametrize('kind', ['missing', 'other_order', 'amount', 'duplicate', 'future'])
def test_invalid_refund_evidence_is_local_review(tmp_path, kind):
    # 为当前边界场景修改隔离测试数据。
    def mutate(case):
        tx = case['history']['refund_transactions'][0]
        if kind == 'missing': case['history']['refund_transactions'] = []
        elif kind == 'other_order': tx['order_id'] = 'other'
        elif kind == 'amount': tx['amount_fen'] = 9999
        elif kind == 'duplicate': case['history']['refund_transactions'].append(dict(tx))
        else: tx['completed_at'] = '2099-01-01T00:00:00+08:00'
    answer = validate_result(decision('refund_status', ['reply'], 'status'), prepare(tmp_path, 6, mutate))
    assert answer['facts']['refund_confirmed'] is False
    assert answer['review_items']
    assert answer['facts']['paid_amount_fen'] == 5990
    assert '流水显示退款成功' not in answer['reply_draft']

# 未查询、越权、伪造工具名不能用作证据。
@pytest.mark.parametrize('kind', ['unqueried', 'forbidden', 'unknown'])
def test_reject_invalid_evidence_reference(tmp_path, kind):
    tools = prepare(tmp_path)
    raw = decision()
    if kind == 'unqueried': tools.observed_results.pop('get_order')
    elif kind == 'forbidden': tools.get_order('another-order')
    else: raw['evidence_refs'].append('external_api')
    with pytest.raises(AnalysisError): validate_result(raw, tools)

# 商品声明与客户反馈不是实际漏液或少件的证据。
@pytest.mark.parametrize('number,issue', [(4, 'leak'), (5, 'missing_item')])
def test_customer_report_does_not_become_verified_damage(tmp_path, number, issue):
    answer = validate_result(decision(issue, ['request_information'], 'none'), prepare(tmp_path, number))
    assert '照片' in answer['reply_draft']
    assert '商品确实漏液' not in answer['reply_draft']
    assert '确定少发' not in answer['reply_draft']
    assert '待核实' in answer['intent']

# 有附件时不无条件重复索取照片。
def test_existing_attachment_needs_inspection_not_reupload(tmp_path):
    # 添加模拟附件以验证不会重复索取照片。
    def attach(case): case['messages'][0]['attachments'] = [{'type': 'image', 'name': 'photo.jpg'}]
    answer = validate_result(decision('leak', ['request_information'], 'requested'), prepare(tmp_path, 4, attach))
    assert '请补充' not in answer['reply_draft']
    assert '附件' in answer['recommendation']

# 非法动作不能落入任何真实写操作或进入网页。
def test_unknown_action_is_rejected(tmp_path):
    with pytest.raises(AnalysisError):
        validate_result(decision(actions=['execute_refund']), prepare(tmp_path))


# 未来签收时间不能作为当前已签收的有效证据。
def test_future_signed_time_not_reported_as_delivered(tmp_path):
    # 为当前边界场景修改隔离测试数据。
    def mutate(case): case['shipments'][0]['signed_at'] = '2099-01-01T00:00:00+08:00'
    answer = validate_result(decision('leak', ['request_information'], 'requested'), prepare(tmp_path, 4, mutate))
    assert answer['facts']['delivery_recorded'] is False


# 事实展示应给出可核查的物流停滞时长，不只显示英文状态。
def test_logistics_age_is_computed_from_snapshot(tmp_path):
    answer = validate_result(decision(), prepare(tmp_path))
    assert answer['facts']['logistics_age_hours'] > 72
    assert any('小时' in item['text'] for item in answer['verified_facts'])


# 条件意向、无意向和查退款状态都不能被当成立即申请授权。
@pytest.mark.parametrize('refund', ['none', 'conditional', 'status'])
def test_no_refund_approval_without_requested_intent(tmp_path, refund):
    answer = validate_result(decision(actions=['check_logistics', 'human_approval'], refund=refund), prepare(tmp_path))
    assert 'human_approval' not in answer['next_actions']
    assert answer['decision_adjustments']


# 分类和意向冲突时只澄清该问题，不捏造明确退款授权。
def test_conditional_refund_request_does_not_claim_explicit_authorization(tmp_path):
    answer = validate_result(decision('refund_request', ['human_approval'], 'conditional'), prepare(tmp_path))
    assert 'human_approval' not in answer['next_actions']
    assert '客户明确的退款申请' not in answer['recommendation']
    assert 'request_information' in answer['next_actions']


# 已全额退款后禁止建议再次申请退款。
def test_full_refund_does_not_offer_repeat_approval(tmp_path):
    answer = validate_result(decision('refund_status', ['reply', 'human_approval'], 'requested'), prepare(tmp_path, 6))
    assert 'human_approval' not in answer['next_actions']
    assert '同时记录退款申请' not in answer['recommendation']
    assert answer['refund_intent'] == 'status'


# 漏液场景确有退款诉求时，允许补证后的人工审核建议。
def test_leak_requested_refund_keeps_conditional_manual_approval(tmp_path):
    answer = validate_result(decision('leak', ['request_information', 'human_approval'], 'requested'), prepare(tmp_path, 4))
    assert 'human_approval' in answer['next_actions']
    assert '证据与规则核实后' in answer['recommendation']


# 异常轨迹不应导致整单崩溃；保留订单事实并明确局部数据问题。
@pytest.mark.parametrize('events', [None, [None], ['invalid']])
def test_malformed_tracking_events_keep_safe_analysis(tmp_path, events):
    # 为当前边界场景修改隔离测试数据。
    def mutate(case): case['shipments'][0]['events'] = events
    answer = validate_result(decision(), prepare(tmp_path, change=mutate))
    assert answer['facts']['paid_amount_fen'] == 5990
    assert 'logistics_age_hours' not in answer['facts']
    assert any(item['field'] == 'shipment_events' for item in answer['review_items'])


# 程序补足模型遗漏的必要步骤也要留下纠正记录，避免评估掩盖模型失误。
def test_added_required_action_is_recorded_as_correction(tmp_path):
    answer = validate_result(decision('shipping_delay', ['check_logistics'], 'conditional'), prepare(tmp_path))
    assert 'next_actions:request_information_added' in answer['decision_adjustments']
