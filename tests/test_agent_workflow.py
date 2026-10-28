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


# 构造符合基本业务约束的模拟分析结果。
def result():
    return {'intent': '物流查询和有条件退款', 'verified_facts': [{'text': '已发货', 'source_ref': 'get_order'}],
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


# 拒绝把当前案例以外的来源写成事实依据。
def test_rejects_unobserved_source(tmp_path):
    storage, case_id, _ = make_run(tmp_path)
    answer = result()
    answer['verified_facts'][0]['source_ref'] = 'external_api'
    with pytest.raises(AnalysisError):
        validate_result(answer, CaseTools(storage, case_id))


# 确认事实来源由已调用工具解析，模型不能自由填写展示名称。
def test_resolves_observed_source_ref(tmp_path):
    storage, case_id, _ = make_run(tmp_path)
    tools = CaseTools(storage, case_id)
    tools.get_order('ORDER-DEMO-20261022-001')
    answer = result()
    answer['verified_facts'][0]['source'] = '伪造来源'
    verified = validate_result(answer, tools)
    assert verified['verified_facts'][0]['source'] == '模拟订单服务'


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


# 确认技术事件同步输出到终端且只包含允许的指标。
def test_console_log_shows_safe_metrics(tmp_path, capsys):
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    logger.event(storage, run_id, 'model_call', 'succeeded',
                 {'duration_ms': 88, 'input_tokens': 3, 'customer_text': 'private-value'})
    output = capsys.readouterr().err
    assert 'model_call' in output
    assert '88' in output
    assert 'private-value' not in output


# 确认模型调用会报告单次响应耗时和 token 用量。
def test_agent_reports_each_model_call(tmp_path, monkeypatch):
    storage, case_id, run_id = make_run(tmp_path)
    observed = []
    # 用固定回复模拟模型，避免真实 API 费用。
    class FakeModel:
        # 接受与真实模型相同的初始化参数。
        def __init__(self, **kwargs):
            pass
        # 返回自身以模拟绑定工具。
        def bind_tools(self, tools):
            return self
        # 返回无事实的合法结果，触发一次模型观测。
        def invoke(self, messages):
            answer = result()
            answer['verified_facts'] = []
            return AIMessage(content=json.dumps(answer, ensure_ascii=False),
                             usage_metadata={'input_tokens': 7, 'output_tokens': 5, 'total_tokens': 12})
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    monkeypatch.setattr(ticket_agent, 'ChatOpenAI', FakeModel)
    tools = CaseTools(storage, case_id, on_model_call=lambda status, metrics: observed.append((status, metrics)))
    ticket_agent.analyze_ticket(case_id, run_id, tools)
    assert observed[0][0] == 'succeeded'
    assert observed[0][1]['duration_ms'] >= 0
    assert observed[0][1]['input_tokens'] == 7


# 确认分析失败也记录总耗时，便于终端排障。
def test_failed_run_logs_total_duration(tmp_path):
    storage, _, run_id = make_run(tmp_path)
    logger = RunLogger(tmp_path / 'agent.jsonl')
    # 模拟安全的分析失败。
    def fail(*args):
        raise AnalysisError('模型输出不是合法 JSON')
    process_run(storage, run_id, fail, logger)
    records = [json.loads(line) for line in (tmp_path / 'agent.jsonl').read_text(encoding='utf-8').splitlines()]
    assert records[-1]['event_type'] == 'run_failed'
    assert records[-1]['duration_ms'] >= 0


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
