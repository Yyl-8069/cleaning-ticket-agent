"""根据已查询记录分类信息缺口；不从未查询的案例原文补造事实。"""
from context.intent_decisions import anchored_deadline, aware_time

CATEGORY_LABELS = {
    'customer_evidence': '客户需补充', 'source_missing': '资料来源缺失',
    'not_queried': '尚未查询', 'normal_absence': '当前无记录',
    'capability_missing': '能力未接入', 'data_conflict': '资料需核对',
    'technical_error': '技术异常',
}


# 将实际查询状态、业务补证和已知能力边界汇总为可追溯的独立事项。
def classify_information_gaps(issues, decisions, tools):
    gaps = []
    observed = tools.observed_results
    ticket = observed.get('get_ticket', {}).get('data') or {}
    ticket = ticket if isinstance(ticket, dict) else {}
    messages = ticket.get('messages', [])
    attachments = any(m.get('attachments') for m in messages if m.get('role') == 'customer')
    order = (observed.get('get_order', {}).get('data') or {}).get('order') or {}

    # 同一来源只登记一次，保留事项类别和处理责任。
    def add(category, code, summary, source_ref=None, owner='客服'):
        if not any(g['code'] == code and g['category'] == category for g in gaps):
            gaps.append({'category': category, 'category_label': CATEGORY_LABELS[category],
                         'code': code, 'summary': summary, 'source_ref': source_ref, 'owner': owner})

    # 未查询、查无资料和技术异常必须依照真实工具状态区分。
    def source(ref, label):
        response = observed.get(ref)
        if response is None:
            add('not_queried', ref, f'本轮尚未查询{label}，需要时由客服补查。', ref)
        elif response['status'] == 'not_found':
            if ref == 'get_shipments' and order.get('status') == 'paid' and not order.get('shipped_at'):
                add('normal_absence', ref, '订单尚未发货，本次查询没有物流记录，不要求客户补运单。', ref)
            else:
                add('source_missing', ref, f'本次查询未取得{label}，请客服核实资料来源；不代表现实中不存在。', ref)
        elif response['status'] != 'found':
            add('technical_error', ref, f'{label}查询未正常完成，请检查查询或授权状态。', ref, '开发/维护')

    source('get_order', '订单记录')
    for issue, raw in zip(issues, decisions):
        kind = issue['issue_type']
        refund = issue['refund_intent']
        if refund != 'none' or kind == 'refund_status' or order.get('refunded_amount_fen', 0) > 0:
            source('get_history', '售后和退款记录')
        if kind == 'shipping_delay' or refund == 'conditional':
            source('get_shipments', '物流记录')
        elif kind == 'shipping_request' and 'get_shipments' in observed:
            source('get_shipments', '物流记录')
        if 'check_policy' in issue['next_actions']:
            source('get_policy', '适用规则')
        if kind in ('leak', 'product_damage', 'missing_item', 'missing_accessory'):
            source('get_product', '商品型号目录')
            if attachments:
                add('capability_missing', 'image_review', '图片识别尚未接入，请客服查看已有附件，不要求客户重复上传。', owner='开发/客服')
            else:
                for text in issue.get('missing_information', []):
                    if '照片' in text:
                        add('customer_evidence', 'photo_' + kind, text, 'get_ticket', '客户/客服')
        if kind == 'shipping_request' or 'check_fulfillment' in issue['next_actions']:
            add('capability_missing', 'fulfillment_records', '备货安排及实际装箱查询尚未接入，需要客服核实；不能由商品目录推断。', owner='开发/客服')
        if kind == 'refund_status':
            add('capability_missing', 'payment_settlement', '支付渠道到账查询尚未接入，请客服引导核对原支付渠道记录。', owner='开发/客服')
        if kind == 'other':
            add('customer_evidence', 'clarify_request', '需要明确客户希望解决的具体问题。', 'get_ticket', '客户/客服')
        if refund == 'conditional' and issue['condition_state'] == 'unknown':
            condition = raw.get('condition') or {}
            deadline = aware_time(condition.get('deadline'))
            snapshot = aware_time(tools.case['metadata']['snapshot_at'])
            anchored = (deadline is not None and snapshot is not None and
                        anchored_deadline(deadline, messages, snapshot))
            if not anchored or condition.get('kind') != 'not_delivered_by':
                add('customer_evidence', 'refund_condition', '请补充或确认退款条件及约定截止时间，现有表述暂不能核实。', 'get_ticket', '客户/客服')
            elif observed.get('get_shipments', {}).get('status') == 'found':
                add('source_missing', 'condition_evidence', '现有记录不足以核实截止后的送达情况，请客服补查物流证据，不要求客户重复说明已知条件。', 'get_shipments')
        for item in issue.get('review_items', []):
            if item['field'] == 'refund_evidence' and observed.get('get_history', {}).get('status') != 'found':
                source('get_history', '售后和退款记录')
            else:
                add('data_conflict', item['field'], item['reason'])
    history = observed.get('get_history', {})
    if history.get('status') == 'found':
        payload = history.get('data')
        if not isinstance(payload, dict):
            add('technical_error', 'history_format', '售后记录格式不受支持，需要维护人员核查数据格式。', 'get_history', '开发/维护')
        elif not payload.get('refund_transactions') and order.get('refunded_amount_fen') == 0:
            add('normal_absence', 'refund_transactions', '本次资料未记录退款流水，订单累计退款为0；无记录不等于已退款。', 'get_history')
    return {'gap_schema_version': 1, 'information_gaps': gaps,
            'customer_requests': [g['summary'] for g in gaps if g['category'] == 'customer_evidence'],
            'development_gaps': [g for g in gaps if g['category'] in ('capability_missing', 'technical_error')]}
