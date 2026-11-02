import json
import pytest
from fastapi.testclient import TestClient
from app import main
from evals.run_evals import ROOT


# 未配置或明显无效的密钥在保存工单前拒绝，不产生无意义失败运行。
@pytest.mark.parametrize('key',[None,'','   ','replace-with-your-key','has space'])
def test_missing_configuration_is_reported_before_upload(tmp_path,monkeypatch,key):
    if key is None: monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    else: monkeypatch.setenv('DEEPSEEK_API_KEY',key)
    calls=[]
    # 配置门禁应在分析器执行前返回。
    def analyze(*args): calls.append(1)
    monkeypatch.setattr(main,'analyze_ticket',analyze)
    path=tmp_path/'config.db'
    with TestClient(main.create_app(path)) as client:
        assert '暂不能分析工单' in client.get('/').text
        assert client.get('/ready').status_code==503
        assert client.get('/health').status_code==200
        response=client.post('/cases/upload',files={'file':('case.json',(ROOT/'data/case-001.json').read_bytes(),'application/json')})
        assert response.status_code==503
    import sqlite3
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM cases').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==0
    assert not calls


# 自检只检查本机配置，不调用模型验证密钥有效性，也不输出密钥内容。
def test_valid_looking_config_does_not_call_model_or_expose_key(tmp_path,monkeypatch,caplog):
    key='test-only-private-key'
    monkeypatch.setenv('DEEPSEEK_API_KEY',key)
    calls=[]
    # 只有实际分析才允许调用，此处自检不能进入。
    def analyze(*args): calls.append(1)
    monkeypatch.setattr(main,'analyze_ticket',analyze)
    with TestClient(main.create_app(tmp_path/'ready.db')) as client:
        page=client.get('/').text
        ready=client.get('/ready')
        assert ready.status_code==200 and ready.json()['status']=='ready'
        assert '暂不能分析工单' not in page
        assert key not in page+ready.text+caplog.text
    assert not calls


# 启动时记录配置问题，页面不需要等到首次工单分析才发现。
def test_startup_reports_missing_config(tmp_path,monkeypatch,caplog):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    with TestClient(main.create_app(tmp_path/'startup.db')):
        pass
    assert '启动配置自检' in caplog.text and '未配置' in caplog.text


# 注入的离线分析器不需要真实密钥，保留测试与本地回放能力。
def test_injected_analyzer_does_not_require_live_key(tmp_path,monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    # 固定分析器只用于检查应用的配置门禁范围。
    def analyze(*args): return {}
    with TestClient(main.create_app(tmp_path/'offline.db',analyze)) as client:
        assert client.get('/ready').status_code==200
        assert '暂不能分析工单' not in client.get('/').text


# 模型名称明显无效也提前提示，响应中不回显环境变量值。
@pytest.mark.parametrize('model',['','bad model'])
def test_invalid_model_name_is_reported(tmp_path,monkeypatch,model):
    monkeypatch.setenv('DEEPSEEK_API_KEY','sk-private-not-for-output')
    monkeypatch.setenv('DEEPSEEK_MODEL',model)
    with TestClient(main.create_app(tmp_path/'model.db')) as client:
        response=client.get('/ready')
        assert response.status_code==503 and '模型名称' in response.json()['detail']['message']
        assert 'sk-private-not-for-output' not in response.text


# 配置缺失只阻止新分析，历史结果仍可读取，便于客服查阅。
def test_historical_result_remains_accessible_without_key(tmp_path,monkeypatch):
    from app.storage import Storage
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    path=tmp_path/'history.db'; storage=Storage(path)
    case=json.loads((ROOT/'data/case-001.json').read_text(encoding='utf-8'))
    case_id=storage.save_case(case); run_id=storage.create_run(case_id)
    storage.set_run(run_id,'succeeded',{'intent':'历史查询结果','reply_draft':'历史回复草稿'})
    with TestClient(main.create_app(path)) as client:
        assert client.get('/ready').status_code==503
        page=client.get('/runs/'+run_id)
        assert page.status_code==200 and '历史回复草稿' in page.text
