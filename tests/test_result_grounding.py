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
    assert '499.00' in answer['reply_draft']
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
    assert answer['facts']['paid_amount_fen'] == 49900
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


# 电器渗水须提供停止使用、勿通电和勿自行拆机的安全提示。
def test_electrical_leak_includes_stop_use_safety_advice(tmp_path):
    answer = validate_result(decision('leak', ['request_information'], 'none'), prepare(tmp_path, 4))
    assert '停止使用' in answer['reply_draft']
    assert '不要通电' in answer['reply_draft']
    assert '自行拆机' in answer['reply_draft']


# 外观损坏与配件缺失使用独立分类，并保留退款人工审核边界。
@pytest.mark.parametrize('number,issue,refund,required', [
    (3, 'product_damage', 'requested', 'human_approval'),
    (5, 'missing_accessory', 'none', 'request_information'),
])
def test_appliance_specific_issue_categories(tmp_path, number, issue, refund, required):
    actions = ['request_information', 'human_approval'] if refund == 'requested' else ['request_information']
    answer = validate_result(decision(issue, actions, refund), prepare(tmp_path, number))
    assert answer['issue_type'] == issue
    assert required in answer['next_actions']

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
    assert answer['facts']['paid_amount_fen'] == 12990
    assert 'logistics_age_hours' not in answer['facts']
    assert any(item['field'] == 'shipment_events' for item in answer['review_items'])


# 程序补足模型遗漏的必要步骤也要留下纠正记录，避免评估掩盖模型失误。
def test_added_required_action_is_recorded_as_correction(tmp_path):
    answer = validate_result(decision('shipping_delay', ['check_logistics'], 'conditional'), prepare(tmp_path))
    assert 'next_actions:request_information_added' in answer['decision_adjustments']


# 订单未发货时应纠正为催发，不得删除正确的备货核查动作。
def test_unshipped_classification_preserves_fulfillment(tmp_path):
    answer = validate_result(decision('shipping_delay', ['check_fulfillment'], 'none'), prepare(tmp_path, 2))
    assert answer['issue_type'] == 'shipping_request'
    assert 'check_fulfillment' in answer['next_actions']
    assert 'check_logistics' not in answer['next_actions']
    assert not any('物流记录' in text for text in answer['missing_information'])
    assert answer['raw_decision']['issues'][0]['issue_type'] == 'shipping_delay'

# 少件应允许客服核查出库，与客户补证并行。
def test_missing_item_preserves_fulfillment_check(tmp_path):
    answer = validate_result(decision('missing_item', ['request_information', 'check_fulfillment'], 'none'), prepare(tmp_path, 5))
    assert 'check_fulfillment' in answer['next_actions']
    assert not any('check_fulfillment_removed' in v for v in answer['decision_adjustments'])

# 明确退款诉求缺订单也应交人工核实，不能静默丢掉申请。
def test_requested_refund_without_order_still_marks_human(tmp_path):
    answer = validate_result(decision('refund_request', ['human_approval'], 'requested'),
                             prepare(tmp_path, 3, lambda c: c.update(order=None)))
    assert answer['issues'][0]['human_required'] is True
    assert answer['issues'][0]['human_reason']
    assert 'human_approval' in answer['next_actions']
    assert '获批' in answer['reply_draft']
    assert '订单' in ' '.join(answer['missing_information'])

# 单纯退款状态查询不要求补无关的规则与物流。
def test_refund_status_has_no_irrelevant_missing_policy(tmp_path):
    answer = validate_result(decision('refund_status', ['reply'], 'status'), prepare(tmp_path, 6))
    assert not answer['issues'][0]['human_required']
    assert not any('规则' in text or '物流' in text for text in answer['missing_information'])

# 多诉求分别分析，人工退款不阻断配送查询。
def test_multi_intent_keeps_independent_outcomes(tmp_path):
    tools = prepare(tmp_path, 3)
    raw = {'issues': [decision('shipping_delay', ['check_logistics'], 'none'),
                      decision('refund_request', ['human_approval'], 'requested')]}
    answer = validate_result(raw, tools)
    assert answer['schema_version'] == 3
    assert len(answer['issues']) == 2
    assert answer['issues'][0]['human_required'] is False
    assert answer['issues'][1]['human_required'] is True
    assert 'check_logistics' in answer['next_actions']
    assert 'human_approval' in answer['next_actions']
    assert answer['status'] == 'succeeded'

# 未到截止日的条件退款不能直接审核，也不能要求无关的退款意向确认。
def test_future_condition_handles_current_shipping_first(tmp_path):
    raw = decision('shipping_delay', ['check_logistics', 'human_approval'], 'conditional')
    raw['condition'] = {'kind': 'not_delivered_by', 'deadline': '2026-10-27T23:59:59+08:00',
                        'evidence_refs': ['get_ticket']}
    answer = validate_result({'issues': [raw]}, prepare(tmp_path))
    issue = answer['issues'][0]
    assert issue['condition_state'] == 'pending'
    assert not issue['human_required']
    assert 'human_approval' not in issue['next_actions']
    assert 'request_information' not in issue['next_actions']
    assert '确认目前是否希望申请退款' not in answer['reply_draft']


# 共用事实区同样不能把早于发货的签收时间当作有效签收。
def test_invalid_delivery_chronology_not_presented_as_fact(tmp_path):
    # 将签收改到发货前，保留其余真实查询结构。
    def change(case):
        case['shipments'][0]['signed_at'] = '2026-10-01T10:00:00+08:00'
    answer = validate_result(decision('leak', ['request_information'], 'none'), prepare(tmp_path, 4, change))
    assert answer['facts']['delivery_recorded'] is False
    assert any(v['field'] == 'delivery_evidence' for v in answer['review_items'])


# 多诉求共享问候和金额仅出现一次，重复运行也不能叠加草稿。
@pytest.mark.parametrize('refund', ['requested', 'conditional'])
def test_multi_reply_shares_intro_and_keeps_each_issue(tmp_path, refund):
    tools = prepare(tmp_path)
    second = decision('refund_request', ['human_approval'] if refund == 'requested' else ['reply'], refund)
    if refund == 'conditional':
        second['condition'] = {'kind': 'not_delivered_by', 'deadline': '2026-10-27T23:59:59+08:00',
                               'evidence_refs': ['get_ticket']}
    raw = {'issues': [decision('shipping_delay', ['check_logistics'], 'none'), second]}
    answer = validate_result(raw, tools)
    reply = answer['reply_draft']
    assert reply.count('您好。') == 1
    assert reply.count('实付 129.90 元') == 1
    assert reply.count('累计退款 0.00 元') == 1
    assert '配送进度' in reply
    if refund == 'requested':
        assert '审核' in reply and '不代表退款获批' in reply
    else:
        assert '条件' in reply
        assert '需要先确认您的退款意向' not in reply
    assert validate_result(raw, tools)['reply_draft'] == reply
    assert len(answer['issues']) == 2
