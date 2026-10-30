import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Storage

SAMPLE = Path(__file__).resolve().parents[1] / 'data/case-001.json'


# 正式来源名称只改变页面展示，不改写已保存来源或原始工单。
@pytest.mark.parametrize('source_ref,label', [
    ('get_ticket', '工单资料'), ('get_order', '订单资料'),
    ('get_product', '商品目录'), ('get_shipments', '物流记录'),
    ('get_history', '售后记录'), ('get_policy', '店铺规则'),
    (None, '上传资料'), ('legacy_source', '上传资料'),
])
def test_formal_source_names_preserve_original_evidence(tmp_path, source_ref, label):
    path = tmp_path / 'app.db'
    client = TestClient(create_app(path))
    storage = Storage(path)
    case = json.loads(SAMPLE.read_text(encoding='utf-8'))
    case_id = storage.save_case(case)
    run_id = storage.create_run(case_id)
    original_source = case['order']['source']
    fact = {'text': '订单资料已核对。', 'source': original_source}
    if source_ref is not None:
        fact['source_ref'] = source_ref
    result = {'intent': '查询订单', 'verified_facts': [fact], 'missing_information': [],
              'recommendation': '核对订单资料', 'reply_draft': '订单资料已收到。', 'policy_version': None}
    storage.set_run(run_id, 'succeeded', result)
    page = client.get(f'/runs/{run_id}')
    assert page.status_code == 200
    assert f'来源：{label}' in page.text
    assert '来源说明：本次上传的工单资料。' in page.text
    assert original_source not in page.text
    assert '<small>规则版本：' in page.text
    assert '模拟' not in page.text
    assert storage.get_run(run_id)['result'] == result
    assert storage.get_case(case_id) == case


# 上传页使用正式工单名称，保留客服确认后使用草稿的说明。
def test_upload_page_uses_business_names(tmp_path):
    page = TestClient(create_app(tmp_path / 'app.db')).get('/')
    assert '<title>工单分析</title>' in page.text
    assert '工单 JSON' in page.text
    assert '模拟' not in page.text
    assert '客服确认' in page.text


# 导入边界说明使用数据样例用语，原有来源标记校验继续生效。
def test_upload_validation_uses_sample_wording(tmp_path):
    case = json.loads(SAMPLE.read_text(encoding='utf-8'))
    case['metadata']['synthetic'] = False
    response = TestClient(create_app(tmp_path / 'app.db')).post(
        '/cases/upload', files={'file': ('case.json', json.dumps(case).encode(), 'application/json')})
    assert response.status_code == 400
    assert 'metadata.synthetic' in response.json()['detail']
    assert '数据样例' in response.json()['detail']
    assert '模拟' not in response.json()['detail']
