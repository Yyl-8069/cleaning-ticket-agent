"""Read-only, case-scoped lookups for the model."""
from app.storage import now
from datetime import datetime


def applicable_policy(case):
    policy = case.get('policy')
    if not policy:
        return None
    snapshot = datetime.fromisoformat(case['metadata']['snapshot_at'])
    start = datetime.fromisoformat(policy['effective_from']) if policy.get('effective_from') else None
    end = datetime.fromisoformat(policy['effective_to']) if policy.get('effective_to') else None
    return policy if (start is None or start <= snapshot) and (end is None or snapshot < end) else None


class CaseTools:
    def __init__(self, storage, case_id, on_call=None):
        self.case = storage.get_case(case_id)
        self.on_call = on_call or (lambda name, status: None)
        self.observed_sources = set()

    def _read(self, name, requested, expected, data, source):
        status = 'forbidden' if requested != expected else ('not_found' if not data else 'found')
        if status == 'found':
            self.observed_sources.add(source)
        self.on_call(name, status)
        return {'status': status, 'data': data if status == 'found' else None,
                'source': source, 'observed_at': self.case['metadata']['snapshot_at']}

    # 按工单号读取当前案例的工单和客户消息。
    def get_ticket(self, ticket_id):
        return self._read('get_ticket', ticket_id, self.case['ticket']['ticket_id'],
                          {'ticket': self.case['ticket'], 'messages': self.case['messages']}, '上传的工单资料')

    # 按订单号返回订单记录及购买商品明细。
    def get_order(self, order_id):
        order = self.case.get('order')
        data = {'order': order, 'order_items': self.case.get('order_items', [])} if order else None
        return self._read('get_order', order_id, self.case['ticket']['order_id'], data,
                          (order or {}).get('source', '订单记录样例'))

    # 按商品编号查询当前案例中的家电目录信息。
    def get_product(self, product_id):
        product = self.case.get('product')
        return self._read('get_product', product_id, (product or {}).get('product_id'), product,
                          (product or {}).get('source', '家电型号目录样例'))

    # 按订单号读取物流记录及已有轨迹。
    def get_shipments(self, order_id):
        shipments = self.case.get('shipments', [])
        return self._read('get_shipments', order_id, self.case['ticket']['order_id'], shipments,
                          shipments[0].get('source', '物流轨迹样例') if shipments else '物流轨迹样例')

    # 按工单号查询关联售后及退款历史。
    def get_history(self, ticket_id):
        history = self.case.get('history')
        return self._read('get_history', ticket_id, self.case['ticket']['ticket_id'], history,
                          (history or {}).get('source', '售后历史样例'))

    # 查询在当前案例快照时间有效的店铺处理参考。
    def get_policy(self, shop_id):
        policy = applicable_policy(self.case)
        return self._read('get_policy', shop_id, self.case['ticket']['shop_id'], policy,
                          (policy or {}).get('source', '项目家电售后处理参考'))
