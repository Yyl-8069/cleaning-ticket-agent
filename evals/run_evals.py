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


def score_answer(answer, scenario):
    text = ' '.join(str(answer.get(k, '')) for k in ('intent', 'recommendation', 'reply_draft'))
    missing = [term for term in scenario['must_contain'] if term not in text]
    forbidden = [term for term in scenario['forbidden'] if term in text]
    return {'passed': not missing and not forbidden, 'missing_terms': missing, 'forbidden_terms': forbidden}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='调用 DeepSeek API 并产生费用')
    args = parser.parse_args()
    base = json.loads((ROOT / 'data' / 'case-001.json').read_text(encoding='utf-8'))
    scenarios = json.loads((ROOT / 'evals' / 'cases' / 'scenarios.json').read_text(encoding='utf-8'))
    if not args.live:
        for scenario in scenarios:
            case = deepcopy(base)
            case.update(scenario['change'])
            parse_case(json.dumps(case, ensure_ascii=False).encode())
        print(f'{len(scenarios)} 个模拟评估输入通过校验；未调用模型，业务准确率未知。')
        return
    storage = Storage(ROOT / 'runtime' / 'evals.db')
    logger = RunLogger(ROOT / 'runtime' / 'evals.jsonl')
    results = []
    for scenario in scenarios:
        case = deepcopy(base)
        case.update(scenario['change'])
        case_id = storage.save_case(parse_case(json.dumps(case, ensure_ascii=False).encode()))
        run_id = storage.create_run(case_id)
        try:
            process_run(storage, run_id, analyze_ticket, logger)
            run = storage.get_run(run_id)
            if run['status'] == 'failed':
                results.append({'id': scenario['id'], 'passed': False, 'error_type': run['error_type']})
                continue
            answer = run['result']
            score = score_answer(answer, scenario)
            results.append({'id': scenario['id'], **score,
                            'token_usage': answer.get('token_usage'), 'duration_ms': answer.get('duration_ms')})
        except Exception as exc:
            results.append({'id': scenario['id'], 'passed': False, 'error_type': type(exc).__name__})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f'通过 {sum(r["passed"] for r in results)}/{len(results)}；该词项评分仍需人工复核事实与语义。')


if __name__ == '__main__':
    main()
