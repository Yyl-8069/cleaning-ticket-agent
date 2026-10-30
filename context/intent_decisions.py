"""校验有限诉求结构，并独立核实退款条件；不提供资金操作。"""
import re
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


# 仅匹配最新客户消息中的单一明确送达条件，复杂或冲突表达保留未知。
def anchored_deadline(deadline, messages, snapshot):
    customers = [m for m in messages if isinstance(m, dict) and m.get('role') == 'customer']
    if not customers:
        return False
    dated = [(aware_time(m.get('created_at')), m) for m in customers]
    if any(moment is None or moment > snapshot for moment, _ in dated):
        return False
    latest = max(moment for moment, _ in dated)
    current = [m for moment, m in dated if moment == latest]
    if len(current) != 1:
        return False
    content = current[0].get('content', '')
    if not isinstance(content, str) or content.count('退款') != 1:
        return False
    if re.search(r'不是|取消|撤回|不退款|不要退款|暂不|不用|[“”"「」]', content):
        return False
    date_pattern = r'今天|明天|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})'
    if len(re.findall(date_pattern, content)) != 1:
        return False
    pattern = (r'(?P<when>' + date_pattern + r')\s*(?:之前|前)?\s*(?:还)?'
               r'(?:没(?:有)?(?:收到|送到)|(?:收|送)不到|不到(?=就|[，,]))'
               r'[^。！？!?；;\n]{0,24}?退款')
    matched = re.search(pattern, content)
    if matched is None:
        return False
    token = matched.group('when')
    if token not in ('今天', '明天'):
        return aware_time(token) == deadline
    try:
        candidate = datetime.combine(latest.date() + timedelta(days=token == '明天'), time(23, 59, 59), latest.tzinfo)
    except OverflowError:
        return False
    return candidate == deadline


# 共用签收核验：校验订单关联、发货与签收先后及轨迹时间，矛盾证据不作事实。
def valid_signed_time(shipment, order, snapshot):
    if (not order or shipment.get('order_id') != order.get('order_id')
            or shipment.get('status') != 'delivered'):
        return None
    signed = aware_time(shipment.get('signed_at'))
    if signed is None or signed > snapshot:
        return None
    for source, field in ((order, 'paid_at'), (order, 'shipped_at'), (shipment, 'shipped_at')):
        if source.get(field) is not None:
            moment = aware_time(source[field])
            if moment is None or moment > signed:
                return None
    events = shipment.get('events', [])
    if not isinstance(events, list):
        return None
    for event in events:
        moment = aware_time(event.get('at')) if isinstance(event, dict) else None
        if moment is None or moment > signed:
            return None
        if moment == signed and event.get('status') == 'in_transit':
            return None
    return signed


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
            or not anchored_deadline(deadline, (ticket.get('data') or {}).get('messages', []), snapshot)):
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
        if shipment.get('status') == 'delivered':
            signed = valid_signed_time(shipment, order, snapshot)
            if signed is None:
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
