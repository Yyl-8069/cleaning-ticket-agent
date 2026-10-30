"""One LangChain tool-calling agent; all business writes belong to the workflow."""
import json
import os
from pathlib import Path
from time import monotonic

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from context.result_grounding import grounded_result

PROMPTS = Path(__file__).resolve().parents[1] / 'prompts'
MAX_TOOL_CALLS = 12
MAX_SECONDS = 90


class AnalysisError(RuntimeError):
    SAFE_REASONS = frozenset({
        '模型输出不是 JSON 对象', '模型输出状态无效', '模型输出业务判断无效', '模型输出业务动作无效', '事实和待确认信息必须为列表',
        '事实缺少内容或来源', '事实缺少来源引用', '事实来源不属于当前案例', '事实来源未经过实际查询',
        '规则版本与案例不一致', '草稿声称执行了未授权业务动作',
        '未设置 DEEPSEEK_API_KEY', 'Agent 运行超时', '模型输出不是文本',
        '模型输出不是合法 JSON', '工具调用次数超限', '请求了未知工具',
        'Agent 未在规定轮次内结束',
    })

    # 只向页面和日志暴露白名单中的失败原因。
    @property
    def safe_reason(self):
        message = str(self)
        if message.startswith('模型输出缺少 ') and message.removeprefix('模型输出缺少 ') in (
            'intent', 'verified_facts', 'missing_information', 'recommendation',
            'reply_draft', 'policy_version', 'status',
        ):
            return message
        return message if message in self.SAFE_REASONS else type(self).__name__


# 校验有限业务决策，并从实际查询结果生成事实和安全回复。
def validate_result(result, tools):
    try:
        answer = grounded_result(result, tools)
    except ValueError as exc:
        raise AnalysisError(str(exc)) from exc
    # 用量字段由调用层记录，不由模型输出决定。
    return answer


# 让单个 LangChain Agent 查询当前案例并生成结构化分析结果。
def analyze_ticket(case_id, run_id, tools):
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
        # 为指定的只读业务方法生成 LangChain 工具函数。
        def make_call(method_name):
            # 执行一次受案例范围约束的工具查询。
            def call(identifier: str) -> str:
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
        call_started = monotonic()
        try:
            response = model.invoke(messages)
        except Exception:
            tools.on_model_call('failed', {'duration_ms': round((monotonic() - call_started) * 1000),
                                           'model': model_name})
            raise
        token = response.usage_metadata or {}
        tools.on_model_call('succeeded', {'duration_ms': round((monotonic() - call_started) * 1000),
                                          'input_tokens': token.get('input_tokens', 0),
                                          'output_tokens': token.get('output_tokens', 0), 'model': model_name})
        if monotonic() - started > MAX_SECONDS:
            raise AnalysisError('Agent 运行超时')
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
            if not isinstance(result, dict):
                raise AnalysisError('模型输出不是 JSON 对象')
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
