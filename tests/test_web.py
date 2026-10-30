from pathlib import Path
from html.parser import HTMLParser
import json

import pytest
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


# 分析中持续刷新当前结果页，所有终态停止；GET不能创建新的运行。
@pytest.mark.parametrize('status,refresh_expected', [
    ('queued', True), ('running', True), ('succeeded', False),
    ('needs_information', False), ('failed', False), ('interrupted', False),
])
def test_result_auto_refresh_only_for_pending_runs(tmp_path, status, refresh_expected):
    from app.storage import Storage
    calls = []

    # 只读页面请求不应触发分析器。
    def unexpected_analysis(*args):
        calls.append(args)
        raise AssertionError('页面刷新不能重新调用模型')

    # 按HTML属性解析刷新配置，不依赖模板的空格或属性顺序。
    class RefreshParser(HTMLParser):
        # 只收集页面中声明的刷新标签。
        def __init__(self):
            super().__init__()
            self.refreshes = []

        # 提取刷新间隔，避免测试只检查包含某个单词。
        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'meta' and attributes.get('http-equiv', '').lower() == 'refresh':
                self.refreshes.append(attributes.get('content'))

    path = tmp_path / 'app.db'
    client = TestClient(create_app(path, unexpected_analysis))
    storage = Storage(path)
    case_id = storage.save_case(json.loads(SAMPLE.read_text(encoding='utf-8')))
    run_id = storage.create_run(case_id)
    storage.set_run(run_id, status, error_type='AnalysisError' if status == 'failed' else None)
    for _ in range(2):
        response = client.get(f'/runs/{run_id}')
        assert response.status_code == 200
        assert response.headers.get('cache-control') == 'no-store'
        parser = RefreshParser()
        parser.feed(response.text)
        assert parser.refreshes == (['2'] if refresh_expected else [])
        if refresh_expected:
            assert '自动更新' in response.text
    assert calls == []
    with storage._db() as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


# 同一个运行由分析中转为完成后，下一次自动请求即可展示结果并停止刷新。
def test_auto_refresh_displays_result_when_run_finishes(tmp_path):
    from app.storage import Storage
    path = tmp_path / 'app.db'
    client = TestClient(create_app(path, fake_analysis))
    storage = Storage(path)
    case_id = storage.save_case(json.loads(SAMPLE.read_text(encoding='utf-8')))
    run_id = storage.create_run(case_id)
    storage.set_run(run_id, 'running')
    assert 'http-equiv="refresh"' in client.get(f'/runs/{run_id}').text
    storage.set_run(run_id, 'succeeded', {
        'intent': '查询物流', 'verified_facts': [], 'missing_information': [],
        'recommendation': '核查物流进度', 'reply_draft': '我们会核查配送进度。',
        'policy_version': None,
    })
    page = client.get(f'/runs/{run_id}')
    assert '核查配送进度' in page.text
    assert '分析完成' in page.text
    assert 'http-equiv="refresh"' not in page.text


# 分项显示人工原因，物流查询仍正常完成，且不声称已派单。
def test_multi_intent_page_shows_manual_handoff(tmp_path):
    # 预设语义判断，仍通过真实查询、校验与网页流程。
    def analyze(case_id, run_id, tools):
        ticket = tools.case['ticket']
        tools.get_ticket(ticket['ticket_id'])
        tools.get_order(ticket['order_id'])
        tools.get_history(ticket['ticket_id'])
        tools.get_policy(ticket['shop_id'])
        tools.get_shipments(ticket['order_id'])
        refs = list(tools.observed_results)
        return {'issues': [
            {'issue_type': 'shipping_delay', 'refund_intent': 'none',
             'next_actions': ['check_logistics'], 'evidence_refs': refs},
            {'issue_type': 'refund_request', 'refund_intent': 'requested',
             'next_actions': ['human_approval'], 'evidence_refs': refs,
             'human_reason': '<script>fake-approval</script>'}]}
    client = TestClient(create_app(tmp_path / 'app.db', analyze))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')})
    assert '诉求 1' in response.text and '诉求 2' in response.text
    assert '需人工处理' in response.text
    assert '交接说明' in response.text
    assert '尚未派单' in response.text
    assert 'fake-approval' not in response.text
    assert '核查物流' in response.text
    assert '分析完成' in response.text
