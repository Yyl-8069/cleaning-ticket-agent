from pathlib import Path
import json

import pytest

from agents.ticket_agent import AnalysisError, validate_result
from agents import ticket_agent
from langchain_core.messages import AIMessage
from app.observability import RunLogger
from app.storage import Storage
from context.case_schema import parse_case
from tools.case_tools import CaseTools
from workflows.analyze_ticket import process_run

SAMPLE = Path(__file__).resolve().parents[1] / 'data' / 'case-001.json'


def make_run(tmp_path):
    storage = Storage(tmp_path / 'app.db')
    case_id = storage.save_case(parse_case(SAMPLE.read_bytes()))
    return storage, case_id, storage.create_run(case_id)


def result():
    return {'intent': '物流查询和有条件退款', 'verified_facts': [{'text': '已发货', 'source': '模拟订单服务'}],
            'missing_information': ['是否现在申请退款'], 'recommendation': '人工核查物流',
            'reply_draft': '请问您现在是否希望申请退款？', 'policy_version': 'DEMO-LOGISTICS-V1',
            'status': 'needs_information'}


def test_rejects_claimed_refund(tmp_path):
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['reply_draft'] = '已经退款，请查收'
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


@pytest.mark.parametrize('claim', ['已提交退款审批', '已发送消息', '已经签收', '已经丢件'])
def test_rejects_unperformed_or_unverified_claims(tmp_path, claim):
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['reply_draft'] = claim
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


def test_rejects_unobserved_source(tmp_path):
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['verified_facts'][0]['source'] = '拼多多官方接口'
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


def test_rejects_source_not_queried(tmp_path):
    storage, case_id, _ = make_run(tmp_path)
    with pytest.raises(AnalysisError, match='实际查询'):
        validate_result(result(), CaseTools(storage, case_id))


def test_failed_model_keeps_failure_without_leaking_secret(tmp_path):
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    def fail(*args):
        raise RuntimeError('secret-key customer raw text')
    process_run(storage, run_id, fail, logger)
    run = storage.get_run(run_id)
    assert run['status'] == 'failed'
    assert run['error_type'] == 'RuntimeError'
    assert 'secret-key' not in (tmp_path / 'agent.jsonl').read_text(encoding='utf-8')


def test_restart_marks_running_interrupted(tmp_path):
    storage, _, run_id = make_run(tmp_path)
    storage.set_run(run_id, 'running')
    assert Storage(tmp_path / 'app.db').get_run(run_id)['status'] == 'interrupted'


def test_log_contains_time_and_safe_usage(tmp_path):
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    logger.event(storage, run_id, 'run_finished', 'needs_information', {'duration_ms': 12, 'input_tokens': 3})
    record = json.loads((tmp_path / 'agent.jsonl').read_text(encoding='utf-8'))
    assert record['at']
    assert record['duration_ms'] == 12
    assert record['input_tokens'] == 3


def test_langchain_agent_calls_scoped_tools(tmp_path, monkeypatch):
    storage, case_id, run_id = make_run(tmp_path)
    calls = []
    class FakeModel:
        def __init__(self, **kwargs):
            pass
        def bind_tools(self, tools):
            assert [item.name for item in tools][:2] == ['get_ticket', 'get_order']
            return self
        def invoke(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                return AIMessage(content='', tool_calls=[
                    {'name': 'get_order', 'args': {'identifier': 'ORDER-DEMO-20261022-001'}, 'id': 'one'},
                    {'name': 'get_policy', 'args': {'identifier': 'SHOP-DEMO-001'}, 'id': 'two'}])
            return AIMessage(content=json.dumps(result(), ensure_ascii=False))
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(ticket_agent, 'ChatOpenAI', FakeModel)
    answer = ticket_agent.analyze_ticket(case_id, run_id, CaseTools(storage, case_id))
    assert answer['status'] == 'needs_information'
    assert len(calls) == 2
