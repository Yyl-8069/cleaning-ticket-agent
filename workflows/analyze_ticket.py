"""Owns run state; model and read-only tools never update business records."""
from time import monotonic

from agents.ticket_agent import AnalysisError, validate_result
from tools.case_tools import CaseTools


# 推进一次分析运行，并保存结果、模型调用指标或安全的失败记录。
def process_run(storage, run_id, analyzer, logger):
    run = storage.get_run(run_id)
    if run['status'] != 'queued':
        raise ValueError('run must be queued')
    started = monotonic()
    storage.set_run(run_id, 'running')
    logger.event(storage, run_id, 'run_started', '开始分析')
    tools = CaseTools(storage, run['case_id'],
                      on_call=lambda name, status: logger.event(storage, run_id, 'tool_call', f'{name}:{status}'),
                      on_model_call=lambda status, metrics: logger.event(storage, run_id, 'model_call', status, metrics))
    try:
        raw = analyzer(run['case_id'], run_id, tools)
        result = validate_result(raw, tools)
        result['token_usage'] = raw.get('token_usage', {'input': 0, 'output': 0})
        result['model'] = raw.get('model', 'test')
        result['prompt_version'] = '2.0'
        for item in result['review_items']:
            logger.event(storage, run_id, 'validation_review', item['field'])
        for correction in result['decision_adjustments']:
            logger.event(storage, run_id, 'decision_adjusted', correction)
        status = 'succeeded'
        result['status'] = status
        result['duration_ms'] = round((monotonic() - started) * 1000)
        storage.set_run(run_id, status, result)
        usage = result.get('token_usage') or {}
        logger.event(storage, run_id, 'run_finished', status,
                     {'duration_ms': result['duration_ms'], 'input_tokens': usage.get('input', 0),
                      'output_tokens': usage.get('output', 0), 'model': result.get('model', 'test')})
    except Exception as exc:
        storage.set_run(run_id, 'failed', error_type=type(exc).__name__)
        logger.event(storage, run_id, 'run_failed',
                     exc.safe_reason if isinstance(exc, AnalysisError) else type(exc).__name__,
                     {'duration_ms': round((monotonic() - started) * 1000)})
