from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

SAMPLE = Path(__file__).resolve().parents[1] / 'data' / 'case-001.json'

def fake_analysis(case_id, run_id, tools):
    order = tools.get_order('ORDER-DEMO-20261022-001')
    return {'intent': '查询物流；如果今天仍未收到，考虑退款',
            'verified_facts': [{'text': '订单已发货', 'source': order['source']}],
            'missing_information': ['是否现在申请退款'],
            'recommendation': '核查物流并询问退款意愿',
            'reply_draft': '您好，我们会先核查物流。请问您现在是否希望申请退款？',
            'policy_version': 'APPLIANCE-AFTERSALES-V1', 'status': 'needs_information',
            'token_usage': {'input': 0, 'output': 0}}

def test_upload_process_and_refresh(tmp_path):
    client = TestClient(create_app(tmp_path / 'app.db', fake_analysis))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers['location'])
    assert '核查物流' in page.text
    assert 'needs_information' in page.text
    assert client.get(response.headers['location']).status_code == 200

def test_invalid_upload_does_not_create_run(tmp_path):
    client = TestClient(create_app(tmp_path / 'app.db', fake_analysis))
    response = client.post('/cases/upload', files={'file': ('bad.json', b'{', 'application/json')})
    assert response.status_code == 400
    assert client.get('/').status_code == 200
