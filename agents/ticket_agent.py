"""One LangChain tool-calling agent; all business writes belong to the workflow."""
import json
import os
from pathlib import Path
from time import monotonic

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from tools.case_tools import applicable_policy

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
MAX_TOOL_CALLS = 12
MAX_SECONDS = 90


class AnalysisError(RuntimeError):
    SAFE_REASONS = frozenset({
        '模型输出不是 JSON 对象', '模型输出状态无效', '事实和待确认信息必须为列表',
        '事实缺少内容或来源', '事实来源不属于当前案例', '事实来源未经过实际查询',
        '规则版本与案例不一致', '草稿声称执行了未授权业务动作',
        '未设置 DEEPSEEK_API_KEY', 'Agent 运行超时', '模型输出不是文本',
        '模型输出不是合法 JSON', '工具调用次数超限', '请求了未知工具',
        'Agent 未在规定轮次内结束',
    })

    @property
    def safe_reason(self):
        """只暴露预设的安全失败原因，避免泄露原始异常信息。"""
        message = str(self)
        if message.startswith('模型输出缺少 ') and message.removeprefix('模型输出缺少 ') in (
            'intent', 'verified_facts', 'missing_information', 'recommendation',
            'reply_draft', 'policy_version', 'status',
        ):
            return message
        return message if message in self.SAFE_REASONS else type(self).__name__


def validate_result(result, tools):
    """校验模型输出结构、事实来源和禁止声称的业务动作。"""
    if not isinstance(result, dict):
        raise AnalysisError('模型输出不是 JSON 对象')
    for name in ('intent', 'verified_facts', 'missing_information', 'recommendation',
                 'reply_draft', 'policy_version', 'status'):
        if name not in result:
            raise AnalysisError(f'模型输出缺少 {name}')
    if result['status'] not in ('succeeded', 'needs_information'):
        raise AnalysisError('模型输出状态无效')
    if not isinstance(result['verified_facts'], list) or not isinstance(result['missing_information'], list):
        raise AnalysisError('事实和待确认信息必须为列表')
    allowed_sources = {'模拟工单快照'}
    for item in ('order', 'product', 'history', 'policy'):
        record = tools.case.get(item)
        if isinstance(record, dict) and record.get('source'):
            allowed_sources.add(record['source'])
    allowed_sources.update(item.get('source') for item in tools.case.get('shipments', []) if item.get('source'))
    for fact in result['verified_facts']:
        if not isinstance(fact, dict) or not fact.get('text') or not fact.get('source'):
            raise AnalysisError('事实缺少内容或来源')
        if fact['source'] not in allowed_sources:
            raise AnalysisError('事实来源不属于当前案例')
        if fact['source'] not in tools.observed_sources:
            raise AnalysisError('事实来源未经过实际查询')
    policy = applicable_policy(tools.case)
    if result['policy_version'] != (policy or {}).get('policy_id'):
        raise AnalysisError('规则版本与案例不一致')
    forbidden = ('已退款', '已经退款', '已提交退款审批', '已发送消息', '已经签收',
                 '已经丢件', '已创建物流核查工单', '已经建单', '已为您退款')
    combined = result['recommendation'] + result['reply_draft'] + ' '.join(f['text'] for f in result['verified_facts'])
    if any(text in combined for text in forbidden):
        raise AnalysisError('草稿声称执行了未授权业务动作')
    return result


def analyze_ticket(case_id, run_id, tools):
    """让单个 LangChain Agent 查询当前案例并生成分析结果。"""
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise AnalysisError('未设置 DEEPSEEK_API_KEY')
    model_name = os.environ.get('DEEPSEEK_MODEL', 'deepseek-flash')
    model = ChatOpenAI(model=model_name, api_key=key, base_url='https://api.deepseek.com',
                       timeout=30, max_retries=0, temperature=0)
    names = ('get_ticket', 'get_order', 'get_product', 'get_shipments', 'get_history', 'get_policy')
    descriptions = {
        'get_ticket': '用工单 ticket_id 查询消息和工单',
        'get_order': '用订单 order_id 查询订单和商品编号',
        'get_product': '用商品 product_id 查询商品',
        'get_shipments': '用订单 order_id 查询物流',
        'get_history': '用工单 ticket_id 查询历史；不能传订单号',
        'get_policy': '用店铺 shop_id 查询当前适用规则',
    }
    wrapped = []
    for name in names:
        def make_call(method_name):
            """为指定的只读业务方法生成 LangChain 工具函数。"""
            def call(identifier: str) -> str:
                """执行一次受案例范围约束的工具查询。"""
                return json.dumps(getattr(tools, method_name)(identifier), ensure_ascii=False)
            return call
        wrapped.append(tool(name, description=f'{descriptions[name]}；identifier 为对应编号，只读')(make_call(name)))
    model = model.bind_tools(wrapped)
    prompt = '\n\n'.join((PROMPTS / name).read_text(encoding='utf-8')
                         for name in ('system.md', 'constraints.md', 'examples.md', 'output-schema.json'))
    case = tools.case
    messages = [SystemMessage(content=prompt), HumanMessage(content=(PROMPTS / 'task.md').read_text(encoding='utf-8').format(
        ticket_id=case['ticket']['ticket_id'], order_id=case['ticket']['order_id'],
        shop_id=case['ticket']['shop_id'], case_id=case_id))]
    started = monotonic()
    usage = {'input': 0, 'output': 0}
    calls = 0
    lookup = {item.name: item for item in wrapped}
    for _ in range(MAX_TOOL_CALLS + 2):
        if monotonic() - started > MAX_SECONDS:
            raise AnalysisError('Agent 运行超时')
        response = model.invoke(messages)
        if monotonic() - started > MAX_SECONDS:
            raise AnalysisError('Agent 运行超时')
        token = response.usage_metadata or {}
        usage['input'] += token.get('input_tokens', 0)
        usage['output'] += token.get('output_tokens', 0)
        messages.append(response)
        if not response.tool_calls:
            content = response.content
            if not isinstance(content, str):
                raise AnalysisError('模型输出不是文本')
            try:
                result = json.loads(content.strip().removeprefix('```json').removesuffix('```').strip())
            except json.JSONDecodeError as exc:
                raise AnalysisError('模型输出不是合法 JSON') from exc
            result = validate_result(result, tools)
            result['token_usage'] = usage
            result['model'] = model_name
            return result
        calls += len(response.tool_calls)
        if calls > MAX_TOOL_CALLS:
            raise AnalysisError('工具调用次数超限')
        for request in response.tool_calls:
            name = request['name']
            if name not in lookup:
                raise AnalysisError('请求了未知工具')
            messages.append(ToolMessage(content=lookup[name].invoke(request['args']),
                                        tool_call_id=request['id']))
    raise AnalysisError('Agent 未在规定轮次内结束')
