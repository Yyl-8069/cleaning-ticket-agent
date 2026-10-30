"""把模型的有限业务判断与已查询事实组合成可交给客服的安全结果。"""
from datetime import datetime

ISSUES = {
    'shipping_delay': '物流停滞或配送进度查询',
    'shipping_request': '未发货催发',
    'refund_request': '申请退款',
    'leak': '客户反馈漏液，实物情况待核实',
    'missing_item': '客户反馈少件，实收情况待核实',
    'refund_status': '查询历史退款及到账情况',
    'other': '其他诉求，需澄清具体问题',
}
ACTIONS = {
    'reply': '向客户说明已核实的记录',
    'request_information': '补充与诉求相关的必要信息',
    'check_policy': '查询适用的店铺售后规则',
    'human_approval': '交客服按适用规则审核退款申请',
    'check_logistics': '核查物流进度和预计配送安排',
    'check_fulfillment': '核查备货与发货安排',
}


# 仅接收结构化决策；自由文本不作为事实或客服回复直接输出。
def check_decision(raw, tools):
    if not isinstance(raw, dict):
        raise ValueError('模型输出不是 JSON 对象')
    if (not isinstance(raw.get('issue_type'), str) or raw['issue_type'] not in ISSUES
            or raw.get('refund_intent') not in ('none', 'conditional', 'requested', 'status')):
        raise ValueError('模型输出业务判断无效')
    actions = raw.get('next_actions')
    if not isinstance(actions, list) or not actions or any(not isinstance(a, str) or a not in ACTIONS for a in actions):
        raise ValueError('模型输出业务动作无效')
    refs = raw.get('evidence_refs')
    if not isinstance(refs, list) or not refs or any(not isinstance(r, str) for r in refs):
        raise ValueError('事实缺少来源引用')
    known = {'get_ticket', 'get_order', 'get_product', 'get_shipments', 'get_history', 'get_policy'}
    for ref in set(refs) | {'get_ticket', 'get_order'}:
        if ref not in known:
            raise ValueError('事实来源不属于当前案例')
        observation = tools.observed_results.get(ref)
        if observation is None or observation['status'] == 'forbidden':
            raise ValueError('事实来源未经过实际查询')


# 从实际查询的响应取数据，不从尚未查询的案例字段补出事实。
def observed_data(tools, ref):
    observation = tools.observed_results.get(ref, {})
    return observation.get('data') if observation.get('status') == 'found' else None


# 仅将同订单、时间有效、唯一且金额一致的成功流水作为历史退款证据。
def refund_evidence(order, history, snapshot):
    if not order or not isinstance(history, dict):
        return False, False
    transactions = history.get('refund_transactions', [])
    if not isinstance(transactions, list):
        return False, True
    successful = [t for t in transactions if isinstance(t, dict) and t.get('status') == 'succeeded']
    total, ids = 0, set()
    for tx in successful:
        amount = tx.get('amount_fen')
        identity = tx.get('transaction_id')
        try:
            completed = datetime.fromisoformat(tx.get('completed_at', ''))
            valid_time = completed.tzinfo is not None and completed <= datetime.fromisoformat(snapshot)
        except (TypeError, ValueError):
            valid_time = False
        if (tx.get('order_id') != order['order_id'] or not isinstance(identity, str) or not identity
                or identity in ids or type(amount) is not int or amount <= 0 or not valid_time):
            return False, True
        ids.add(identity)
        total += amount
    refund = order['refunded_amount_fen']
    if total != refund or total > order['paid_amount_fen']:
        return False, True
    return total > 0, False


# 解析不晚于案例快照的带时区时间，拒绝未来或无效状态证据。
def past_time(value, snapshot):
    try:
        moment = datetime.fromisoformat(value)
        return moment if moment.tzinfo is not None and moment <= snapshot else None
    except (TypeError, ValueError):
        return None


# 用查询到的结构化字段生成事实、局部核验事项与具体处理步骤。
def grounded_result(raw, tools):
    check_decision(raw, tools)
    issue = raw['issue_type']
    snapshot = datetime.fromisoformat(tools.case['metadata']['snapshot_at'])
    order_data = observed_data(tools, 'get_order') or {}
    order = order_data.get('order')
    history = observed_data(tools, 'get_history')
    policy = observed_data(tools, 'get_policy')
    shipments = observed_data(tools, 'get_shipments') or []
    ticket_data = observed_data(tools, 'get_ticket') or {}
    messages = ticket_data.get('messages', [])
    attachments = any(m.get('attachments') for m in messages)
    facts, verified, missing, review = {}, [], [], []
    adjustments = []
    refund_intent = raw['refund_intent']
    if issue == 'refund_status' and refund_intent != 'status':
        refund_intent = 'status'
        adjustments.append('refund_intent:normalized_to_status')

    # 每条展示事实的来源由已查询工具确定，模型不能自填来源名称。
    def fact(key, value, text, ref):
        facts[key] = value
        verified.append({'key': key, 'value': value, 'text': text, 'source_ref': ref,
                         'source': tools.observed_results[ref]['source']})

    if order:
        fact('order_status', order.get('status'), f"订单快照状态：{order.get('status')}。", 'get_order')
        for key, label in (('paid_amount_fen', '实付'), ('refunded_amount_fen', '累计退款')):
            fact(key, order[key], f"订单快照显示{label} {order[key] / 100:.2f} 元。", 'get_order')
    else:
        missing.append('当前快照缺少订单记录，需要核实订单资料。')
    for ref, label in (('get_policy', '适用规则'), ('get_shipments', '物流记录'), ('get_history', '售后历史')):
        observation = tools.observed_results.get(ref)
        if not observation or observation['status'] != 'found':
            missing.append(f'当前查询未取得{label}，不能据此判断现实中不存在。')
    if shipments:
        statuses = [s.get('status') for s in shipments]
        fact('shipment_statuses', statuses, '物流快照状态：' + '、'.join(str(s) for s in statuses) + '。', 'get_shipments')
        delivered = all(s.get('status') == 'delivered' and past_time(s.get('signed_at'), snapshot) for s in shipments)
        fact('delivery_recorded', bool(delivered),
             '物流记录显示已签收；不证明实收商品数量或完好。' if delivered else '当前物流记录不能确认全部包裹已签收。', 'get_shipments')
        event_times, invalid_events = [], False
        for shipment in shipments:
            events = shipment.get('events', [])
            if not isinstance(events, list):
                invalid_events = True
                continue
            for event in events:
                moment = past_time(event.get('at'), snapshot) if isinstance(event, dict) else None
                if moment is None:
                    invalid_events = True
                else:
                    event_times.append(moment)
        if invalid_events:
            review.append({'field': 'shipment_events', 'reason': '物流轨迹含无效或未来时间，暂不计算停滞时长，请核查轨迹。'})
        elif event_times:
            hours = round((snapshot - max(event_times)).total_seconds() / 3600, 1)
            fact('logistics_age_hours', hours, f'最后一条有效物流更新距案例快照约 {hours} 小时；不等于确认丢件。', 'get_shipments')
    product = observed_data(tools, 'get_product')
    if product:
        fact('product_specification', product.get('sku_description'),
             f"商品目录规格：{product.get('sku_description', '未提供')}；不证明实际装箱内容。", 'get_product')
    confirmed, conflict = refund_evidence(order, history, tools.case['metadata']['snapshot_at'])
    facts['refund_confirmed'] = confirmed
    if confirmed:
        fact('refund_confirmed', True, '订单金额与成功退款流水一致；流水显示退款成功，但无法确认到账。', 'get_history')
    elif conflict or (order and order['refunded_amount_fen'] > 0 and history is None):
        review.append({'field': 'refund_evidence', 'reason': '订单退款金额与可验证的退款流水不一致或流水缺失，请核对退款记录。'})
    can_request_refund = bool(refund_intent == 'requested' and issue != 'refund_status' and order
                              and order['paid_amount_fen'] > order['refunded_amount_fen'] and not conflict)
    # shortcut: 首版只覆盖七类售后诉求；新增业务类型需同时扩展枚举、话术及评估基线。
    required = {
        'shipping_delay': ['check_logistics'], 'shipping_request': ['check_fulfillment'],
        'refund_request': ['human_approval'] if can_request_refund else ['request_information'],
        'leak': ['request_information'], 'missing_item': ['request_information'],
        'refund_status': ['reply'], 'other': ['request_information'],
    }[issue]
    if confirmed and order['paid_amount_fen'] == order['refunded_amount_fen'] and issue == 'refund_request':
        required = ['reply']
    if refund_intent == 'conditional' and 'request_information' not in required:
        required.append('request_information')
    allowed = set(required) | {'reply', 'check_policy'}
    if can_request_refund:
        allowed.add('human_approval')
    actions = []
    for action in dict.fromkeys(raw['next_actions']):
        if action in allowed:
            actions.append(action)
        else:
            adjustments.append('next_actions:' + action + '_removed')
    for action in required:
        if action not in actions:
            actions.append(action)
            adjustments.append('next_actions:' + action + '_added')
    if not policy and issue != 'refund_status' and 'check_policy' not in actions:
        actions.append('check_policy')
        adjustments.append('next_actions:check_policy_added')
    recommendation, reply = [], ['您好。']
    if order:
        reply.append(f"订单记录显示实付 {order['paid_amount_fen'] / 100:.2f} 元，累计退款 {order['refunded_amount_fen'] / 100:.2f} 元。")
    else:
        reply.append('当前资料缺少订单记录，需要先核实订单。')
    if issue == 'shipping_request':
        recommendation.append('请客服核查备货与发货安排，确认后回复客户；不要承诺未经确认的发货时间。')
        reply.append('关于发货安排，需要客服核查后向您反馈。')
    elif issue == 'shipping_delay':
        recommendation.append('请客服核查物流进度与预计配送安排；物流停滞不能证明丢件。')
        reply.append('关于配送进度，需要进一步核查物流，当前无法承诺具体送达时间。')
    elif issue == 'refund_request':
        if confirmed and order['paid_amount_fen'] == order['refunded_amount_fen']:
            recommendation.append('记录显示已全额退款，请说明历史退款记录，不要重复发起退款。')
            reply.append('流水显示退款成功，但当前资料无法确认到账，请查看原支付渠道的退款记录。')
        elif can_request_refund:
            recommendation.append('记录客户明确的退款申请，交客服结合订单和适用规则审核；本次未执行退款。')
            reply.append('您的退款诉求需要客服根据订单及适用规则审核，当前回复不代表退款获批。')
        else:
            recommendation.append('请先确认退款意向及订单退款记录，确认前不提交审核或执行退款。')
            reply.append('需要先确认您的退款意向及订单记录，再确定下一步处理。')

    elif issue in ('leak', 'missing_item'):
        subject = '漏液位置及外包装' if issue == 'leak' else '实收商品全貌及外包装'
        if attachments:
            recommendation.append(f'请客服检查现有附件，核对{subject}；Agent未识别图片，不能确认损坏或少件。')
            reply.append('已收到您提供的附件，需要进一步核实商品及包装情况。')
        else:
            missing.append(f'需要{subject}照片。')
            recommendation.append(f'请客户补充{subject}照片，再核对订单规格与处理规则。')
            reply.append(f'为核实您反馈的情况，请补充{subject}照片。')
    elif issue == 'refund_status':
        recommendation.append('向客户说明现有退款记录，到账结果需查询支付渠道；不要再次发起退款。')
        reply.append('流水显示退款成功，但当前资料无法确认到账，请查看原支付渠道的退款记录。' if confirmed
                     else '当前证据不足以确认退款处理完成，需要核对退款流水；无法确认到账。')
    else:
        missing.append('需要明确客户希望解决的具体问题。')
        reply.append('请说明您希望查询或处理的具体问题。')
        recommendation.append('请客服澄清具体诉求后再处理。')
    if refund_intent == 'conditional':
        recommendation.append('客户提出有条件退款，不应直接当成立即退款授权；必要时确认条件是否已满足。')
        reply.append('关于您提出的条件退款，请确认目前是否希望申请退款。')
    elif can_request_refund and issue != 'refund_request':
        recommendation.append('同时记录退款申请，证据与规则核实后交客服审核，不自动执行退款。')
        if 'human_approval' not in actions:
            actions.append('human_approval')
            adjustments.append('next_actions:human_approval_added')
    if not policy and issue != 'refund_status':
        recommendation.append('当前缺少适用规则，请客服补充规则后确定退款、补发等方案。')
    if review:
        recommendation.extend(item['reason'] for item in review)
    return {'schema_version': 2, 'issue_type': issue, 'refund_intent': refund_intent, 'decision_adjustments': adjustments,
            'intent': ISSUES[issue], 'facts': facts, 'verified_facts': verified,
            'missing_information': missing, 'next_actions': actions,
            'action_labels': [ACTIONS[a] for a in actions], 'review_items': review,
            'recommendation': ' '.join(recommendation), 'reply_draft': ''.join(reply),
            'policy_version': (policy or {}).get('policy_id'), 'status': 'succeeded',
            'evidence_refs': list(dict.fromkeys(raw['evidence_refs']))}
