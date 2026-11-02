import json
import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from openai import APIStatusError, APITimeoutError
from agents import ticket_agent
from agents.ticket_agent import AnalysisError
from app.main import create_app
from app.observability import RunLogger
from app.storage import Storage
from evals.run_evals import ROOT, evaluation_cases, offline_analyzer
from context.result_grounding import grounded_result
from tools.case_tools import CaseTools
from workflows.analyze_ticket import process_run


# 使用独立评估工单创建隔离数据，避免修改用户运行库。
def case_run(tmp_path, index=2):
    _, case, expected = evaluation_cases('independent')[index]
    storage = Storage(tmp_path/'safety.db')
    case_id = storage.save_case(case)
    return storage, case_id, storage.create_run(case_id), expected


# 创建SDK标准服务端错误，不携带真实连接或密钥。
def service_error(code):
    response = httpx.Response(code, request=httpx.Request('POST','https://example.invalid'))
    return APIStatusError('private error text', response=response, body=None)


# 暂时性服务异常仅恢复一次，认证或参数错误不会重试。
@pytest.mark.parametrize('error,recover,expected_calls', [
    (500,True,2),(503,False,2),(429,True,2),(401,False,1),(400,False,1),('timeout',True,2)])
def test_service_retry_is_bounded(tmp_path, monkeypatch, error, recover, expected_calls):
    storage, case_id, run_id, _ = case_run(tmp_path)
    calls=[]; observations=[]
    class Model:
        # 接收真实模型配置，避免请求网络。
        def __init__(self, **kwargs): pass
        # 模拟只读工具绑定。
        def bind_tools(self, tools): return self
        # 按预设产生错误与恢复结果，并记录单次超时预算。
        def invoke(self, messages, **kwargs):
            calls.append(kwargs)
            if len(calls)==1 or not recover:
                if error=='timeout':
                    raise APITimeoutError(request=httpx.Request('POST','https://example.invalid'))
                raise service_error(error)
            return AIMessage(content=json.dumps({'issue_type':'leak','refund_intent':'none',
                'next_actions':['request_information'],'evidence_refs':['get_ticket','get_order']}),
                usage_metadata={'input_tokens':7,'output_tokens':3,'total_tokens':10})
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.setattr(ticket_agent,'ChatOpenAI',Model)
    monkeypatch.setattr(ticket_agent,'sleep',lambda _:None,raising=False)
    tools=CaseTools(storage,case_id,on_model_call=lambda status,metrics:observations.append((status,metrics)))
    if recover:
        answer=ticket_agent.analyze_ticket(case_id,run_id,tools)
        assert answer['token_usage']=={'input':7,'output':3}
    else:
        with pytest.raises(APIStatusError):
            ticket_agent.analyze_ticket(case_id,run_id,tools)
    assert len(calls)==expected_calls
    assert all(0 < item['timeout'] <= 30 for item in calls)
    assert sum(status=='failed' for status,_ in observations)==(1 if recover else expected_calls)


# 剩余总时间不足以等待时立即终止，不启动额外请求。
def test_service_retry_respects_total_deadline(tmp_path,monkeypatch):
    storage,case_id,run_id,_=case_run(tmp_path)
    clock=[0.0]; calls=[]
    class Model:
        # 使用可控时钟测试时限。
        def __init__(self,**kwargs): pass
        # 保留工具接口。
        def bind_tools(self,tools): return self
        # 模拟请求接近耗尽总预算。
        def invoke(self,messages,**kwargs):
            calls.append(kwargs['timeout']); clock[0]=4.9
            raise service_error(500)
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.setattr(ticket_agent,'ChatOpenAI',Model)
    monkeypatch.setattr(ticket_agent,'MAX_SECONDS',5)
    monkeypatch.setattr(ticket_agent,'monotonic',lambda:clock[0])
    with pytest.raises(AnalysisError,match='超时'):
        ticket_agent.analyze_ticket(case_id,run_id,CaseTools(storage,case_id))
    assert calls==[5]


# 恢复一次后若后续轮次再次失败，不再重试，保留用量且不重复执行工具。
def test_retry_budget_is_shared_across_rounds_and_keeps_partial_usage(tmp_path,monkeypatch):
    storage,case_id,run_id,_=case_run(tmp_path)
    calls=[]
    class Model:
        # 避免真实模型连接。
        def __init__(self,**kwargs): pass
        # 保留工具绑定接口。
        def bind_tools(self,tools): return self
        # 一次恢复后返回工具请求，下一轮再次错误应直接失败。
        def invoke(self,messages,**kwargs):
            calls.append(1)
            if len(calls)!=2: raise service_error(503)
            case=storage.get_case(case_id)
            return AIMessage(content='',tool_calls=[
                {'name':'get_ticket','args':{'identifier':case['ticket']['ticket_id']},'id':'ticket'},
                {'name':'get_order','args':{'identifier':case['ticket']['order_id']},'id':'order'}],
                usage_metadata={'input_tokens':9,'output_tokens':4,'total_tokens':13})
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    monkeypatch.setattr(ticket_agent,'ChatOpenAI',Model)
    monkeypatch.setattr(ticket_agent,'sleep',lambda _:None)
    process_run(storage,run_id,ticket_agent.analyze_ticket,RunLogger(tmp_path/'retry.jsonl'))
    run=storage.get_run(run_id)
    assert len(calls)==3 and run['status']=='failed' and run['result'] is None
    assert run['metrics']['input_tokens']==9 and run['metrics']['output_tokens']==4
    assert run['metrics']['usage_complete'] is False
    assert sum(e['event_type']=='tool_call' for e in run['events'])==2
    assert any(e['event_type']=='safety_notice' for e in run['events'])
    assert 'private error text' not in (tmp_path/'retry.jsonl').read_text(encoding='utf-8')


# 裂纹、焦味等危险描述触发规则提示，普通物流和工具正文不会触发。
@pytest.mark.parametrize('text,risky', [('烤箱门玻璃有裂纹',True),('外壳裂开了',True),
    ('熨斗有焦味还异常发热',True),('热水器正在漏水',True),('快递还没到',False)])
def test_risk_notice_covers_dangerous_descriptions(text,risky):
    from context.safety import customer_safety_notice
    notice=customer_safety_notice({'messages':[{'role':'customer','content':text}]})
    assert bool(notice)==risky
    if risky:
        assert '停止使用' in notice and '不要通电' in notice and '自行拆机' in notice
    assert not customer_safety_notice({'messages':[{'role':'tool','content':text}]})


# 高风险工单模型失败仍展示安全提醒，且页面不展示模型草稿。
def test_failed_page_keeps_safety_notice_without_model_content(tmp_path):
    storage,case_id,_,_=case_run(tmp_path)
    case=storage.get_case(case_id)
    # 模拟已确认的外部服务错误。
    def fail(*args): raise service_error(500)
    app=create_app(tmp_path/'web.db',analyzer=fail)
    with TestClient(app) as client:
        page=client.post('/cases/upload',files={'file':('risk.json',json.dumps(case).encode(),'application/json')})
    text=page.text
    assert '停止使用' in text and '分析失败' in text
    assert text.index('停止使用') < text.index('分析失败')
    assert '请客服接手核查' in text
    assert '回复草稿' not in text and 'private error text' not in text
    assert '模型服务暂时不可用' in text


# 成功回复也将风险提示置于金额与补证前，且多诉求汇总不会重复。
@pytest.mark.parametrize('index',[8,16])
def test_safety_precedes_money_and_photos_in_reply(tmp_path,index):
    storage,case_id,run_id,expected=case_run(tmp_path,index)
    tools=CaseTools(storage,case_id)
    raw=offline_analyzer(expected)(case_id,run_id,tools)
    answer=grounded_result(raw,tools)
    assert answer['reply_draft'].index('停止使用') < answer['reply_draft'].index('订单记录')
    assert answer['reply_draft'].count('为安全起见')==1
    assert answer['recommendation'].startswith('为安全起见')
