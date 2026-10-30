"""校验有限诉求结构，并独立核实退款条件；不提供资金操作。"""
from datetime import datetime, time, timedelta


# 丢弃模型自填金额与自由回复，只接收有限的分类、动作和条件字段。
def normalize_decisions(raw: dict) -> list[dict]:
    from context.result_grounding import ISSUES, ACTIONS
    if not isinstance(raw, dict):
        raise ValueError('模型输出不是 JSON 对象')
    items = raw.get('issues') if 'issues' in raw else [raw]
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        raise ValueError('模型输出业务判断无效')
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('模型输出业务判断无效')
        issue, refund = item.get('issue_type'), item.get('refund_intent')
        if not isinstance(issue, str) or issue not in ISSUES or refund not in ('none', 'conditional', 'requested', 'status'):
            raise ValueError('模型输出业务判断无效')
        actions, refs = item.get('next_actions'), item.get('evidence_refs')
        if not isinstance(actions, list) or not actions or any(not isinstance(a, str) or a not in ACTIONS for a in actions):
            raise ValueError('模型输出业务动作无效')
        if not isinstance(refs, list) or not refs or any(not isinstance(r, str) for r in refs):
            raise ValueError('事实缺少来源引用')
        condition = item.get('condition')
        if condition is not None:
            if (not isinstance(condition, dict) or condition.get('kind') not in ('not_delivered_by', 'unspecified')
                    or (condition.get('deadline') is not None and not isinstance(condition['deadline'], str))
                    or not isinstance(condition.get('evidence_refs'), list)
                    or any(not isinstance(r, str) for r in condition['evidence_refs'])):
                raise ValueError('模型输出业务判断无效')
            condition = {k: condition.get(k) for k in ('kind', 'deadline', 'evidence_refs')}
        result.append({'issue_type': issue, 'refund_intent': refund, 'next_actions': list(dict.fromkeys(actions)),
                       'evidence_refs': list(dict.fromkeys(refs)), 'condition': condition})
    return result


# 只接受带时区的时间，格式或类型无效时返回未知。
def aware_time(value):
    try:
        moment = datetime.fromisoformat(value)
        return moment if moment.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


# 将条件截止时间与客户原文关联，拒绝模型凭空设置的截止时间。
def anchored_deadline(deadline, messages):
    for message in messages:
        if message.get('role') != 'customer':
            continue
        content = message.get('content', '')
        if not isinstance(content, str) or '退款' not in content:
            continue
        if deadline.isoformat() in content:
            return True
        created = aware_time(message.get('created_at'))
        if created is None:
            continue
        for word, offset in (('今天', 0), ('明天', 1)):
            candidate = datetime.combine(created.date() + timedelta(days=offset), time(23, 59, 59), created.tzinfo)
            if word in content and deadline == candidate:
                return True
    return False


# 用本轮资料核实送达期限条件，缺少记录不能直接当作未送达证据。
def evaluate_condition(condition, tools) -> dict:
    if condition is None:
        return {'state': 'not_applicable', 'reason': '无条件退款判断。'}
    unknown = {'state': 'unknown', 'reason': '缺少可核实的退款条件、截止时间或物流依据。'}
    if condition.get('kind') != 'not_delivered_by':
        return unknown
    snapshot = aware_time(tools.case['metadata']['snapshot_at'])
    deadline = aware_time(condition.get('deadline'))
    ticket = tools.observed_results.get('get_ticket', {})
    if (snapshot is None or deadline is None or ticket.get('status') != 'found'
            or not anchored_deadline(deadline, (ticket.get('data') or {}).get('messages', []))):
        return unknown
    if deadline > snapshot:
        return {'state': 'unmet', 'reason': '依据客户消息确定的截止时间尚未到达，不代表未来一定送达。'}
    order_response = tools.observed_results.get('get_order', {})
    shipment_response = tools.observed_results.get('get_shipments', {})
    if order_response.get('status') != 'found' or shipment_response.get('status') != 'found':
        return unknown
    order = (order_response.get('data') or {}).get('order')
    shipments = shipment_response.get('data')
    if not order or not isinstance(shipments, list) or not shipments:
        return unknown
    states = []
    for shipment in shipments:
        if not isinstance(shipment, dict) or shipment.get('order_id') != order['order_id']:
            return unknown
        signed = aware_time(shipment.get('signed_at'))
        if shipment.get('status') == 'delivered':
            if signed is None or signed > snapshot:
                return unknown
            states.append('unmet' if signed <= deadline else 'met')
            continue
        if shipment.get('signed_at') is not None or shipment.get('status') != 'in_transit':
            return unknown
        events = shipment.get('events')
        if not isinstance(events, list) or not events:
            return unknown
        moments = [aware_time(e.get('at')) if isinstance(e, dict) else None for e in events]
        if any(m is None or m > snapshot for m in moments):
            return unknown
        latest = max(zip(moments, events), key=lambda pair: pair[0])
        if latest[0] < deadline or latest[1].get('status') != 'in_transit':
            return unknown
        states.append('met')
    if all(s == 'unmet' for s in states):
        return {'state': 'unmet', 'reason': '有效记录显示全部包裹在截止前已签收；不代表商品完好或数量正确。'}
    return {'state': 'met', 'reason': '有效记录显示至少一个包裹在截止后才签收或截止后仍在运输中，需人工核实处理。'}
