"""Owns run state; model and read-only tools never update business records."""
from time import monotonic

from agents.ticket_agent import AnalysisError, validate_result, is_transient_model_error
from tools.case_tools import CaseTools
from context.safety import customer_safety_notice


# 推进一次分析运行，并保存结果、模型调用指标或安全的失败记录。
def process_run(storage, run_id, analyzer, logger):
    run = storage.get_run(run_id)
    if run['status'] != 'queued':
        raise ValueError('run must be queued')
    started = monotonic()
    storage.set_run(run_id, 'running')
    logger.event(storage, run_id, 'run_started', '开始分析')
    metrics = {'input_tokens': 0, 'output_tokens': 0, 'model': 'unknown', 'usage_complete': True}
    # 每次响应即保存用量，后续解析失败也能保留；无响应的失败调用用量未知。
    def record_model(status, values):
        for key in ('input_tokens', 'output_tokens'):
            metrics[key] += values.get(key, 0)
        metrics['model'] = values.get('model', metrics['model'])
        if status == 'failed' or values.get('usage_complete') is False:
            metrics['usage_complete'] = False
        storage.set_run(run_id, 'running', metrics=metrics)
        logger.event(storage, run_id, 'model_call', status, values)
    tools = CaseTools(storage, run['case_id'],
                      on_call=lambda name, status: logger.event(storage, run_id, 'tool_call', f'{name}:{status}'),
                      on_model_call=record_model)
    if customer_safety_notice(tools.case):
        logger.event(storage, run_id, 'safety_notice', '客户描述涉及电器安全风险，提示停用并人工核查')
    try:
        raw = analyzer(run['case_id'], run_id, tools)
        result = validate_result(raw, tools)
        result['token_usage'] = raw.get('token_usage', {'input': 0, 'output': 0})
        result['model'] = raw.get('model', 'test')
        result['prompt_version'] = '3.3'
        for item in result['review_items']:
            logger.event(storage, run_id, 'validation_review', item['field'])
        for correction in result['decision_adjustments']:
            logger.event(storage, run_id, 'decision_adjusted', correction)
        status = 'succeeded'
        result['status'] = status
        result['duration_ms'] = round((monotonic() - started) * 1000)
        metrics['duration_ms'] = result['duration_ms']
        storage.set_run(run_id, status, result, metrics=metrics)
        usage = result.get('token_usage') or {}
        logger.event(storage, run_id, 'run_finished', status,
                     {'duration_ms': result['duration_ms'], 'input_tokens': usage.get('input', 0),
                      'output_tokens': usage.get('output', 0), 'model': result.get('model', 'test')})
    except Exception as exc:
        metrics['duration_ms'] = round((monotonic() - started) * 1000)
        storage.set_run(run_id, 'failed', error_type=type(exc).__name__, metrics=metrics)
        summary = (exc.safe_reason if isinstance(exc, AnalysisError) else
                   '模型服务暂时不可用，请稍后重试或交客服处理。' if is_transient_model_error(exc) else type(exc).__name__)
        logger.event(storage, run_id, 'run_failed',
                     summary, metrics)
