import json
from pathlib import Path

import pytest

from context.case_schema import CaseValidationError, parse_case
from tools.case_tools import CaseTools
from app.storage import Storage

SAMPLE = Path(__file__).resolve().parents[1] / 'data' / 'case-001.json'

def sample():
    """加载可独立修改的标准模拟案例。"""
    return json.loads(SAMPLE.read_text(encoding='utf-8'))

def test_accepts_case_001():
    """确认标准案例能通过输入校验。"""
    case = parse_case(SAMPLE.read_bytes())
    assert case['ticket']['ticket_id'] == 'TK-DEMO-001'
    assert case['metadata']['synthetic'] is True

def test_rejects_invalid_json():
    """确认损坏的 JSON 在导入时被拒绝。"""
    with pytest.raises(CaseValidationError):
        parse_case(b'{')

def test_rejects_mismatched_order_customer():
    """防止订单客户与工单客户不一致。"""
    data = sample()
    data['order']['customer_id'] = 'other'
    with pytest.raises(CaseValidationError, match='order.customer_id'):
        parse_case(json.dumps(data).encode())

def test_rejects_oversize_upload():
    """确认上传文件受大小上限约束。"""
    with pytest.raises(CaseValidationError, match='1 MiB'):
        parse_case(b' ' * (1024 * 1024 + 1))

def test_storage_keeps_independent_runs(tmp_path):
    """确认同一案例的多次分析各有运行记录。"""
    db = Storage(tmp_path / 'app.db')
    case_id = db.save_case(parse_case(SAMPLE.read_bytes()))
    a, b = db.create_run(case_id), db.create_run(case_id)
    assert a != b
    assert db.get_run(a)['status'] == 'queued'

def test_tools_scope_and_missing_order(tmp_path):
    """区分查无订单和越权查询。"""
    db = Storage(tmp_path / 'app.db')
    case = sample()
    case['order'] = None
    case_id = db.save_case(parse_case(json.dumps(case).encode()))
    tools = CaseTools(db, case_id)
    assert tools.get_order(case['ticket']['order_id'])['status'] == 'not_found'
    assert tools.get_order('OTHER')['status'] == 'forbidden'

def test_tools_report_source(tmp_path):
    """确认物流查询返回可追溯的数据来源。"""
    db = Storage(tmp_path / 'app.db')
    case_id = db.save_case(parse_case(SAMPLE.read_bytes()))
    result = CaseTools(db, case_id).get_shipments('ORDER-DEMO-20261028-001')
    assert result['status'] == 'found'
    assert result['source'] == '物流轨迹样例'


def test_rejects_mismatched_product_shop():
    """防止跨店铺商品混入当前案例。"""
    data = sample()
    data['product']['shop_id'] = 'OTHER'
    with pytest.raises(CaseValidationError, match='product.shop_id'):
        parse_case(json.dumps(data).encode())


def test_order_lookup_exposes_item_product_id(tmp_path):
    """确认订单查询提供后续商品查询所需编号。"""
    db = Storage(tmp_path / 'app.db')
    case_id = db.save_case(parse_case(SAMPLE.read_bytes()))
    found = CaseTools(db, case_id).get_order('ORDER-DEMO-20261028-001')
    assert found['data']['order_items'][0]['product_id'] == 'PRODUCT-DEMO-001'


def test_expired_policy_is_not_applicable(tmp_path):
    """确认过期规则不会被当作当前规则返回。"""
    data = sample()
    data['policy']['effective_to'] = '2026-10-26T00:00:00+08:00'
    db = Storage(tmp_path / 'app.db')
    case_id = db.save_case(parse_case(json.dumps(data).encode()))
    assert CaseTools(db, case_id).get_policy(data['shop']['shop_id'])['status'] == 'not_found'


@pytest.mark.parametrize('change,path', [({'metadata': {'snapshot_at': 1}}, 'metadata.snapshot_at'),
                                         ({'shipments': None}, 'shipments')])
def test_malformed_types_return_validation_error(change, path):
    """确认字段类型错误会变成可定位的校验错误。"""
    data = sample()
    if 'metadata' in change:
        data['metadata'].update(change['metadata'])
    else:
        data.update(change)
    with pytest.raises(CaseValidationError, match=path):
        parse_case(json.dumps(data).encode())


def test_queued_run_is_interrupted_after_restart(tmp_path):
    """确认服务重启后遗留队列不会一直显示待处理。"""
    db = Storage(tmp_path / 'app.db')
    case_id = db.save_case(parse_case(SAMPLE.read_bytes()))
    run_id = db.create_run(case_id)
    assert Storage(tmp_path / 'app.db').get_run(run_id)['status'] == 'interrupted'
