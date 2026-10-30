from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

SAMPLE = Path(__file__).resolve().parents[1] / 'data' / 'case-001.json'

# 用固定分析结果代替收费模型完成网页流程测试。
def fake_analysis(case_id, run_id, tools):
    tools.get_ticket(tools.case['ticket']['ticket_id'])
    tools.get_order(tools.case['ticket']['order_id'])
    return {'issue_type': 'shipping_delay', 'refund_intent': 'conditional',
            'next_actions': ['check_logistics'], 'evidence_refs': ['get_ticket', 'get_order'],
            'token_usage': {'input': 3, 'output': 2}}

# 确认上传后能分析、跳转并在刷新后读取结果。
def test_upload_process_and_refresh(tmp_path):
    client = TestClient(create_app(tmp_path / 'app.db', fake_analysis))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers['location'])
    assert '核查物流' in page.text
    assert '分析完成' in page.text
    assert '下一步' in page.text
    assert '总耗时' in page.text
    assert '+00:00' not in page.text
    assert client.get(response.headers['location']).status_code == 200

# 确认无效 JSON 在进入分析前返回错误。
def test_invalid_upload_does_not_create_run(tmp_path):
    client = TestClient(create_app(tmp_path / 'app.db', fake_analysis))
    response = client.post('/cases/upload', files={'file': ('bad.json', b'{', 'application/json')})
    assert response.status_code == 400
    assert client.get('/').status_code == 200


# 确认分析失败只展示原因和耗时，不暴露未校验的模型内容或原始 UTC 时间。
def test_failure_page_shows_safe_summary_and_retry(tmp_path):
    from agents.ticket_agent import AnalysisError
    # 模拟模型输出校验失败。
    def fail(case_id, run_id, tools):
        raise AnalysisError('事实来源不属于当前案例')
    client = TestClient(create_app(tmp_path / 'app.db', fail))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')}, follow_redirects=False)
    page = client.get(response.headers['location'])
    assert '总耗时' in page.text
    assert '重新上传' in page.text
    assert '事实来源' in page.text
    assert '+00:00' not in page.text


# 退款金额与流水冲突只标记局部问题，分析仍正常完成。
def test_review_page_shows_safe_handoff(tmp_path):
    import json
    case = json.loads(SAMPLE.read_text(encoding='utf-8'))
    case['order']['refunded_amount_fen'] = 5990
    client = TestClient(create_app(tmp_path / 'app.db', fake_analysis))
    response = client.post('/cases/upload', files={'file': ('case.json', json.dumps(case).encode(), 'application/json')}, follow_redirects=False)
    page = client.get(response.headers['location'])
    assert '分析完成' in page.text
    assert '需核对的具体事项' in page.text
    assert '累计退款 59.90 元' in page.text
    assert '流水缺失' in page.text


# 旧版本结果不改写、不误标为新版本成功。
def test_old_result_still_displays(tmp_path):
    import json
    from app.storage import Storage
    path = tmp_path / 'app.db'
    storage = Storage(path)
    case_id = storage.save_case(json.loads(SAMPLE.read_text(encoding='utf-8')))
    run_id = storage.create_run(case_id)
    storage.set_run(run_id, 'needs_information', {'intent':'旧分析', 'verified_facts':[],
        'missing_information':[], 'recommendation':'旧建议', 'reply_draft':'旧草稿',
        'review_required':True, 'review_note':'旧复核原因'})
    page = TestClient(create_app(path, fake_analysis)).get(f'/runs/{run_id}')
    assert '旧建议' in page.text
    assert '待人工复核' in page.text


# 新运行的业务待办和用量必须保存，不能因归一化丢失模型用量。
def test_new_run_preserves_usage_and_business_actions(tmp_path):
    from app.storage import Storage
    path = tmp_path / 'app.db'
    client = TestClient(create_app(path, fake_analysis))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')}, follow_redirects=False)
    run_id = response.headers['location'].split('/')[-1]
    run = Storage(path).get_run(run_id)
    assert run['status'] == 'succeeded'
    assert run['result']['token_usage']['input'] == 3
    assert 'check_logistics' in run['result']['next_actions']


# 导入模块或创建默认应用不能改写用户正在运行的数据库。
def test_default_app_storage_is_deferred_until_server_start(monkeypatch):
    import app.main as module
    # 捕获服务器启动前不应发生的默认数据库初始化。
    def fail(*args, **kwargs):
        raise AssertionError('不能在服务器启动前初始化真实数据库')
    monkeypatch.setattr(module, 'Storage', fail)
    assert module.create_app() is not None
