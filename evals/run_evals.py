"""Versioned synthetic business checks. --live incurs model API usage."""
import argparse
import json
from copy import deepcopy
from pathlib import Path

from agents.ticket_agent import analyze_ticket
from app.storage import Storage
from app.observability import RunLogger
from context.case_schema import parse_case
from tools.case_tools import CaseTools
from workflows.analyze_ticket import process_run

ROOT = Path(__file__).resolve().parents[1]


# 对业务决策、金额和待办做确定性评分；自由话术仍需人工语义复核。
def score_answer(answer, scenario):
    if 'required_facts' not in scenario:
        # 保留旧评分接口供历史回归使用，不将词项命中称为事实准确率。
        text = ' '.join(str(answer.get(k, '')) for k in ('intent', 'recommendation', 'reply_draft'))
        missing = [term for term in scenario['must_contain'] if term not in text]
        forbidden = [term for term in scenario['forbidden'] if term in text]
        return {'passed': not missing and not forbidden, 'missing_terms': missing, 'forbidden_terms': forbidden}
    failures = []
    for key, expected in scenario['required_facts'].items():
        if answer.get('facts', {}).get(key) != expected:
            failures.append('fact:' + key)
    for key in ('issue_type', 'refund_intent'):
        if answer.get(key) != scenario[key]:
            failures.append(key)
    for action in scenario['expected_actions']:
        if action not in answer.get('next_actions', []):
            failures.append('action:' + action)
    for action in scenario.get('forbidden_actions', []):
        if action in answer.get('next_actions', []):
            failures.append('forbidden_action:' + action)
    if answer.get('decision_adjustments'):
        failures.append('model_decision_required_correction')
    if answer.get('status') != 'succeeded':
        failures.append('analysis_status')
    if answer.get('review_items'):
        failures.append('unexpected_review')
    return {'passed': not failures, 'failures': failures, 'semantic_review': 'required'}


# 合并六个原始工单与五个历史边界用例，使用不同编号避免混淆。
def evaluation_cases():
    baselines = json.loads((ROOT / 'evals/analysis_expectations.json').read_text(encoding='utf-8'))
    cases = [(row['case_file'], json.loads((ROOT / 'data' / row['case_file']).read_text(encoding='utf-8')), row)
             for row in baselines]
    base = cases[0][1]
    legacy = json.loads((ROOT / 'evals/cases/scenarios.json').read_text(encoding='utf-8'))
    for item in legacy:
        case = deepcopy(base)
        case.update(item['change'])
        expected = deepcopy(baselines[0])
        if case.get('order') is None:
            expected['required_facts'] = {'refund_confirmed': False}
        if case.get('policy') is None:
            expected['expected_actions'].append('check_policy')
        if item['id'] == 'CASE-005':
            expected['refund_intent'] = 'none'
            expected['expected_actions'] = ['check_logistics']
        cases.append(('legacy-' + item['id'], case, expected))
    return cases


# 离线回放用预设判断验证数据流，明确不冒充真实模型识别能力。
def offline_analyzer(expected):
    # 查询全部工具，再用预设语义判断进入与线上相同的校验和工作流。
    def analyze(case_id, run_id, tools):
        case = tools.case
        ticket = case['ticket']
        tools.get_ticket(ticket['ticket_id'])
        tools.get_order(ticket['order_id'])
        tools.get_shipments(ticket['order_id'])
        tools.get_history(ticket['ticket_id'])
        tools.get_policy(ticket['shop_id'])
        if case.get('product'):
            tools.get_product(case['product']['product_id'])
        return {'issue_type': expected['issue_type'], 'refund_intent': expected['refund_intent'],
                'next_actions': expected['expected_actions'], 'evidence_refs': list(tools.observed_results)}
    return analyze


# 校验固定输入，或在独立评估库中执行真实模型/预设判断回放。
def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--live', action='store_true', help='调用 DeepSeek API 并产生费用')
    group.add_argument('--offline-replay', action='store_true', help='预设业务判断，只验证流程，不调用模型')
    args = parser.parse_args()
    cases = evaluation_cases()
    for _, case, _ in cases:
        parse_case(json.dumps(case, ensure_ascii=False).encode())
    if not args.live and not args.offline_replay:
        print(f'{len(cases)} 个模拟输入通过校验（6个原始工单+5个历史边界案例）；未调用模型，业务准确率未知。')
        return
    if args.live:
        import os
        if not os.environ.get('DEEPSEEK_API_KEY'):
            parser.error('未设置 DEEPSEEK_API_KEY；没有发起模型调用。')
    mode = 'live' if args.live else 'offline'
    storage = Storage(ROOT / 'runtime' / f'evals-{mode}.db')
    logger = RunLogger(ROOT / 'runtime' / f'evals-{mode}.jsonl')
    results = []
    for name, case, expected in cases:
        case_id = storage.save_case(case)
        run_id = storage.create_run(case_id)
        analyzer = analyze_ticket if args.live else offline_analyzer(expected)
        process_run(storage, run_id, analyzer, logger)
        run = storage.get_run(run_id)
        answer = run['result'] or {}
        score = score_answer(answer, expected)
        called = {e['summary'].partition(':')[0] for e in run['events'] if e['event_type'] == 'tool_call'}
        missing_tools = sorted(set(expected.get('required_tools', [])) - called)
        if missing_tools:
            score['passed'] = False
            score['failures'].extend('tool:' + t for t in missing_tools)
        results.append({'id': name, 'run_id': run_id, 'mode': mode, **score,
                        'error_type': run['error_type'], 'token_usage': answer.get('token_usage'),
                        'duration_ms': answer.get('duration_ms'), 'cost': 'unknown' if args.live else 'not_applicable',
                        'model': answer.get('model'), 'prompt_version': answer.get('prompt_version'),
                        'scorer_version': 2})
    report = ROOT / 'runtime' / f'evals-{mode}-summary.json'
    report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f'结构化检查通过 {sum(r["passed"] for r in results)}/{len(results)}；模式：{mode}。')
    print('预设判断回放不能证明模型识别正确；真实模型通过后仍需人工语义复核。')
    if not all(r['passed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
