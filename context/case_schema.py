"""Validate a single synthetic ticket bundle at the upload boundary."""
import json
from datetime import datetime

MAX_BYTES = 1024 * 1024


class CaseValidationError(ValueError):
    pass


def _need(obj, path):
    value = obj
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value or value[part] in ('', None):
            raise CaseValidationError(f'{path}: 缺少必要字段')
        value = value[part]
    return value


def parse_case(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise CaseValidationError('文件超过 1 MiB 上限')
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaseValidationError('JSON 格式无效或不是 UTF-8') from exc
    if not isinstance(data, dict):
        raise CaseValidationError('根节点必须是对象')
    for path in ('metadata.case_id', 'metadata.snapshot_at', 'ticket.ticket_id',
                 'ticket.shop_id', 'ticket.customer_id', 'ticket.order_id',
                 'customer.customer_id', 'customer.shop_id', 'shop.shop_id'):
        _need(data, path)
    if data['metadata'].get('synthetic') is not True:
        raise CaseValidationError('metadata.synthetic: 只接受明确标记的模拟案例')
    try:
        snapshot = datetime.fromisoformat(data['metadata']['snapshot_at'])
        if snapshot.tzinfo is None:
            raise ValueError('timezone required')
    except (ValueError, TypeError) as exc:
        raise CaseValidationError('metadata.snapshot_at: 时间格式无效') from exc
    ticket = data['ticket']
    for path, expected in (('customer.customer_id', ticket['customer_id']),
                           ('customer.shop_id', ticket['shop_id']),
                           ('shop.shop_id', ticket['shop_id'])):
        if _need(data, path) != expected:
            raise CaseValidationError(f'{path}: 与工单不匹配')
    order = data.get('order')
    if order is not None:
        for field, expected in (('order_id', ticket['order_id']),
                                ('customer_id', ticket['customer_id']),
                                ('shop_id', ticket['shop_id'])):
            if not isinstance(order, dict) or order.get(field) != expected:
                raise CaseValidationError(f'order.{field}: 与工单不匹配')
        for field in ('paid_amount_fen', 'refunded_amount_fen'):
            amount = order.get(field)
            if type(amount) is not int or amount < 0:
                raise CaseValidationError(f'order.{field}: 金额必须为非负整数分')
        if order['refunded_amount_fen'] > order['paid_amount_fen']:
            raise CaseValidationError('order.refunded_amount_fen: 超过实付金额')
    messages = data.get('messages')
    if not isinstance(messages, list) or not messages:
        raise CaseValidationError('messages: 至少包含一条消息')
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict) or msg.get('ticket_id') != ticket['ticket_id'] or not msg.get('content'):
            raise CaseValidationError(f'messages.{i}: 消息缺失或工单编号不匹配')
    product = data.get('product')
    if product is not None:
        if not isinstance(product, dict) or product.get('shop_id') != ticket['shop_id']:
            raise CaseValidationError('product.shop_id: 与工单不匹配')
    items = data.get('order_items', [])
    if not isinstance(items, list):
        raise CaseValidationError('order_items: 必须是列表')
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise CaseValidationError(f'order_items.{i}: 必须是对象')
        if item.get('order_id') != ticket['order_id']:
            raise CaseValidationError(f'order_items.{i}.order_id: 与工单不匹配')
        if product and item.get('product_id') != product.get('product_id'):
            raise CaseValidationError(f'order_items.{i}.product_id: 与商品不匹配')
    shipments = data.get('shipments', [])
    if not isinstance(shipments, list):
        raise CaseValidationError('shipments: 必须是列表')
    for i, shipment in enumerate(shipments):
        if not isinstance(shipment, dict):
            raise CaseValidationError(f'shipments.{i}: 必须是对象')
        if shipment.get('order_id') != ticket['order_id']:
            raise CaseValidationError(f'shipments.{i}.order_id: 与工单不匹配')
    policy = data.get('policy')
    if policy is not None:
        if not isinstance(policy, dict) or policy.get('shop_id') != ticket['shop_id']:
            raise CaseValidationError('policy.shop_id: 与工单不匹配')
        for field in ('effective_from', 'effective_to'):
            value = policy.get(field)
            if value is not None:
                try:
                    moment = datetime.fromisoformat(value)
                    if moment.tzinfo is None:
                        raise ValueError('timezone required')
                except (ValueError, TypeError) as exc:
                    raise CaseValidationError(f'policy.{field}: 时间格式无效') from exc
    return data
