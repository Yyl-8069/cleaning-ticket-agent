from types import SimpleNamespace
import pytest
from context.intent_decisions import normalize_decisions, evaluate_condition

# 构造最小合法判断，退款执行字段不属于此接口。
def item():
    return {'issue_type': 'shipping_delay', 'refund_intent': 'conditional',
            'next_actions': ['check_logistics'], 'evidence_refs': ['get_ticket', 'get_order']}

# 仅保留允许字段，避免模型自填金额、批准或回复进入可信结果。
def test_normalize_discards_untrusted_fields():
    raw = item() | {'approved_amount': 1000000, 'refund_executed': True, 'reply_draft': '已批准10000元'}
    assert normalize_decisions({'issues': [raw], 'approved': True}) == [item() | {'condition': None}]
    assert normalize_decisions(item()) == [item() | {'condition': None}]

# 空列表、过多诉求与未知动作不能进入业务处理。
@pytest.mark.parametrize('raw', [{'issues': []}, {'issues': [item()] * 9},
    {'issues': [item() | {'next_actions': ['refund_order']}]},
    {'issues': [item() | {'refund_intent': []}]},
    {'issues': [item() | {'condition': {'kind': 'always', 'deadline': None, 'evidence_refs': []}}]}])
def test_reject_invalid_decisions(raw):
    with pytest.raises(ValueError): normalize_decisions(raw)

# 用工具观测构造可核查条件，案例原始字段不能替代查询。
def observations(content, snapshot='2026-10-27T10:00:00+08:00', shipments=None):
    return SimpleNamespace(case={'metadata': {'snapshot_at': snapshot}}, observed_results={
        'get_ticket': {'status': 'found', 'data': {'messages': [{'role': 'customer',
            'content': content, 'created_at': '2026-10-26T10:00:00+08:00'}]}},
        'get_order': {'status': 'found', 'data': {'order': {'order_id': 'O1', 'status': 'shipped'}}},
        'get_shipments': {'status': 'found' if shipments else 'not_found', 'data': shipments}})

# 无条件、未来条件、无证据条件必须区分。
def test_condition_states():
    tools = observations('2026-10-28T18:00:00+08:00还没送到就退款')
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-28T18:00:00+08:00',
                 'evidence_refs': ['get_ticket', 'get_order', 'get_shipments']}
    assert evaluate_condition(None, tools)['state'] == 'not_applicable'
    assert evaluate_condition(condition, tools)['state'] == 'unmet'
    condition['deadline'] = '2026-10-26T18:00:00+08:00'
    assert evaluate_condition(condition, tools)['state'] == 'unknown'
    tools = observations('2026-10-26T18:00:00+08:00还没送到就退款')
    assert evaluate_condition(condition, tools)['state'] == 'unknown'

# 只有来源一致且截止后仍明确运输中的有效记录才允许判条件成立。
def test_condition_met_requires_positive_current_tracking():
    shipment = {'order_id': 'O1', 'status': 'in_transit', 'signed_at': None,
                'events': [{'at': '2026-10-27T09:00:00+08:00', 'status': 'in_transit'}]}
    tools = observations('2026-10-26T18:00:00+08:00还没送到就退款', shipments=[shipment])
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-26T18:00:00+08:00',
                 'evidence_refs': ['get_ticket', 'get_order', 'get_shipments']}
    assert evaluate_condition(condition, tools)['state'] == 'met'
    shipment['order_id'] = 'another-order'
    assert evaluate_condition(condition, tools)['state'] == 'unknown'

# 签收在截止前应判未满足；未来签收时间不能证明条件状态。
def test_delivery_condition_uses_valid_signed_time():
    shipment = {'order_id': 'O1', 'status': 'delivered',
                'signed_at': '2026-10-26T17:00:00+08:00', 'events': []}
    tools = observations('2026-10-26T18:00:00+08:00还没送到就退款', shipments=[shipment])
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-26T18:00:00+08:00',
                 'evidence_refs': ['get_ticket', 'get_order', 'get_shipments']}
    assert evaluate_condition(condition, tools)['state'] == 'unmet'
    shipment['signed_at'] = '2099-01-01T00:00:00+08:00'
    assert evaluate_condition(condition, tools)['state'] == 'unknown'

# 相对日期必须按客户消息日期解释，不能以运行当天猜测。
def test_relative_deadline_requires_exact_message_anchor():
    tools = observations('今天还没送到就退款')
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-26T23:59:59+08:00',
                 'evidence_refs': ['get_ticket']}
    assert evaluate_condition(condition, tools)['state'] == 'unknown'
    tools.observed_results['get_shipments']['status'] = 'forbidden'
    assert evaluate_condition(condition, tools)['state'] == 'unknown'


# 日期与退款同现不代表日期属于送达期限，避免把漏液条件误判为物流条件。
def test_unrelated_date_cannot_anchor_delivery_condition():
    shipment = {'order_id': 'O1', 'status': 'in_transit', 'signed_at': None,
                'events': [{'at': '2026-10-27T09:00:00+08:00', 'status': 'in_transit'}]}
    tools = observations('我在2026-10-26T18:00:00+08:00买的，如果瓶子漏液就退款。', shipments=[shipment])
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-26T18:00:00+08:00', 'evidence_refs': ['get_ticket']}
    assert evaluate_condition(condition, tools)['state'] == 'unknown'

# 未来、无效或缺失的客户消息时间都不能成为退款条件依据。
@pytest.mark.parametrize('created', [None, 'invalid', '2099-01-01T10:00:00+08:00'])
def test_condition_rejects_untrusted_message_time(created):
    tools = observations('2026-10-28T18:00:00+08:00还没送到就退款')
    tools.observed_results['get_ticket']['data']['messages'][0]['created_at'] = created
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-28T18:00:00+08:00', 'evidence_refs': ['get_ticket']}
    assert evaluate_condition(condition, tools)['state'] == 'unknown'

# 更新后的客户条件取代旧说法，不能继续按历史条件自动认定成立。
def test_later_customer_message_invalidates_old_condition_anchor():
    shipment = {'order_id': 'O1', 'status': 'in_transit', 'signed_at': None,
                'events': [{'at': '2026-10-27T09:00:00+08:00', 'status': 'in_transit'}]}
    tools = observations('2026-10-26T18:00:00+08:00还没送到就退款', shipments=[shipment])
    tools.observed_results['get_ticket']['data']['messages'].append({'role': 'customer',
        'content': '先不要退款了，我只查物流。', 'created_at': '2026-10-27T09:30:00+08:00'})
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-26T18:00:00+08:00', 'evidence_refs': ['get_ticket']}
    assert evaluate_condition(condition, tools)['state'] == 'unknown'

# 签收早于发货或与后续运输记录冲突时，不得据此否定退款条件。
def test_inconsistent_signed_evidence_is_unknown():
    shipment = {'order_id': 'O1', 'status': 'delivered', 'signed_at': '2026-10-01T17:00:00+08:00',
                'events': [{'at': '2026-10-27T09:00:00+08:00', 'status': 'in_transit'}]}
    tools = observations('2026-10-26T18:00:00+08:00还没送到就退款', shipments=[shipment])
    tools.observed_results['get_order']['data']['order']['shipped_at'] = '2026-10-23T09:00:00+08:00'
    condition = {'kind': 'not_delivered_by', 'deadline': '2026-10-26T18:00:00+08:00', 'evidence_refs': ['get_ticket']}
    assert evaluate_condition(condition, tools)['state'] == 'unknown'
