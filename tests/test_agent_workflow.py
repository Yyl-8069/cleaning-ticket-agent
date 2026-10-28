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
    """创建带标准案例的临时数据库和待处理运行。"""
    storage = Storage(tmp_path / 'app.db')
    case_id = storage.save_case(parse_case(SAMPLE.read_bytes()))
    return storage, case_id, storage.create_run(case_id)


def result():
    """构造符合基本业务约束的模拟分析结果。"""
    return {'intent': '物流查询和有条件退款', 'verified_facts': [{'text': '已发货', 'source': '模拟订单服务'}],
            'missing_information': ['是否现在申请退款'], 'recommendation': '人工核查物流',
            'reply_draft': '请问您现在是否希望申请退款？', 'policy_version': 'DEMO-LOGISTICS-V1',
            'status': 'needs_information'}


def test_rejects_claimed_refund(tmp_path):
    """防止草稿声称尚未执行的退款。"""
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['reply_draft'] = '已经退款，请查收'
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


@pytest.mark.parametrize('claim', ['已提交退款审批', '已发送消息', '已经签收', '已经丢件'])
def test_rejects_unperformed_or_unverified_claims(tmp_path, claim):
    """拒绝未执行动作或未证实物流结论的声明。"""
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['reply_draft'] = claim
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


def test_rejects_unobserved_source(tmp_path):
    """拒绝把当前案例以外的来源写成事实依据。"""
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['verified_facts'][0]['source'] = '拼多多官方接口'
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


def test_rejects_source_not_queried(tmp_path):
    """拒绝引用尚未通过工具查询的来源。"""
    storage, case_id, _ = make_run(tmp_path)
    with pytest.raises(AnalysisError, match='实际查询'):
        validate_result(result(), CaseTools(storage, case_id))


def test_failed_model_keeps_failure_without_leaking_secret(tmp_path):
    """确认模型异常变成失败状态且日志不泄露异常原文。"""
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    def fail(*args):
        """模拟包含敏感文本的模型异常。"""
        raise RuntimeError('secret-key customer raw text')
    process_run(storage, run_id, fail, logger)
    run = storage.get_run(run_id)
    assert run['status'] == 'failed'
    assert run['error_type'] == 'RuntimeError'
    assert 'secret-key' not in (tmp_path / 'agent.jsonl').read_text(encoding='utf-8')


def test_known_analysis_failure_records_safe_reason(tmp_path):
    """确认预设分析错误能留下可展示的安全原因。"""
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    def fail(*args):
        """模拟已列入安全白名单的分析错误。"""
        raise AnalysisError('模型输出不是合法 JSON')
    process_run(storage, run_id, fail, logger)
    assert storage.get_run(run_id)['events'][-1]['summary'] == '模型输出不是合法 JSON'


def test_unknown_analysis_message_is_not_logged(tmp_path):
    """确认未知错误消息不会原样进入日志。"""
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    def fail(*args):
        """模拟可能包含密钥的未知分析错误。"""
        raise AnalysisError('secret-key customer raw text')
    process_run(storage, run_id, fail, logger)
    assert 'secret-key' not in (tmp_path / 'agent.jsonl').read_text(encoding='utf-8')


def test_restart_marks_running_interrupted(tmp_path):
    """确认重启时正在运行的任务标记为中断。"""
    storage, _, run_id = make_run(tmp_path)
    storage.set_run(run_id, 'running')
    assert Storage(tmp_path / 'app.db').get_run(run_id)['status'] == 'interrupted'


def test_log_contains_time_and_safe_usage(tmp_path):
    """确认日志保留时间和允许的用量指标。"""
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    logger.event(storage, run_id, 'run_finished', 'needs_information', {'duration_ms': 12, 'input_tokens': 3})
    record = json.loads((tmp_path / 'agent.jsonl').read_text(encoding='utf-8'))
    assert record['at']
    assert record['duration_ms'] == 12
    assert record['input_tokens'] == 3


def test_langchain_agent_calls_scoped_tools(tmp_path, monkeypatch):
    """用假模型验证 Agent 的受限工具调用循环。"""
    storage, case_id, run_id = make_run(tmp_path)
    calls = []
    class FakeModel:
        def __init__(self, **kwargs):
            """代替真实模型初始化，避免外部请求。"""
            pass
        def bind_tools(self, tools):
            """核对绑定的只读工具并返回假模型。"""
            assert [item.name for item in tools][:2] == ['get_ticket', 'get_order']
            return self
        def invoke(self, messages):
            """先请求工具，再返回固定结构化答案。"""
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
