from pathlib import Path
from fastapi.testclient import TestClient
from app.main import create_app

SAMPLE = Path(__file__).resolve().parents[1] / 'data' / 'case-001.json'

# 用固定分析结果代替收费模型完成网页流程测试。
def fake_analysis(case_id, run_id, tools):
    order = tools.get_order('ORDER-DEMO-20261022-001')
    return {'intent': '查询物流；如果今天仍未收到，考虑退款',
            'verified_facts': [{'text': '订单已发货', 'source_ref': 'get_order'}],
            'missing_information': ['是否现在申请退款'],
            'recommendation': '核查物流并询问退款意愿',
            'reply_draft': '您好，我们会先核查物流。请问您现在是否希望申请退款？',
            'policy_version': 'DEMO-LOGISTICS-V1', 'status': 'needs_information',
            'token_usage': {'input': 0, 'output': 0}}

# 确认上传后能分析、跳转并在刷新后读取结果。
def test_upload_process_and_refresh(tmp_path):
    client = TestClient(create_app(tmp_path / 'app.db', fake_analysis))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers['location'])
    assert '核查物流' in page.text
    assert '待补充信息' in page.text
    assert '总耗时' in page.text
    assert '+00:00' not in page.text
    assert client.get(response.headers['location']).status_code == 200

def test_invalid_upload_does_not_create_run(tmp_path):
    """确认无效 JSON 在进入分析前返回错误。"""
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


# 待复核分析向客服展示安全建议和订单退款快照，不显示未经证实的断言。
def test_review_page_shows_safe_handoff(tmp_path):
    # 模拟模型在内部建议中写出未核实的退款结论。
    def unsafe_analysis(case_id, run_id, tools):
        tools.get_order('ORDER-DEMO-20261022-001')
        answer = fake_analysis(case_id, run_id, tools)
        answer['recommendation'] = '订单已退款，结束工单'
        return answer
    client = TestClient(create_app(tmp_path / 'app.db', unsafe_analysis))
    response = client.post('/cases/upload', files={'file': ('case.json', SAMPLE.read_bytes(), 'application/json')}, follow_redirects=False)
    page = client.get(response.headers['location'])
    assert '待人工复核' in page.text
    assert '累计退款 0.00 元' in page.text
    assert '订单已退款，结束工单' not in page.text
    assert '核对订单、退款及物流' in page.text
