import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.storage import Storage
from tools.case_tools import CaseTools
from context.result_grounding import grounded_result
from evals.run_evals import evaluation_cases, offline_analyzer


# 从指定独立工单建立可控制查询范围的隔离环境。
def analyze(tmp_path,index,skip=()):
    _,case,expected=evaluation_cases('independent')[index]
    storage=Storage(tmp_path/'gaps.db'); case_id=storage.save_case(case)
    tools=CaseTools(storage,case_id)
    raw=offline_analyzer(expected)(case_id,'test-run',tools)
    for ref in skip:
        tools.observed_results.pop(ref,None)
    for issue in raw['issues']:
        issue['evidence_refs']=[r for r in issue['evidence_refs'] if r not in skip]
        if issue.get('condition'):
            issue['condition']['evidence_refs']=[r for r in issue['condition']['evidence_refs'] if r not in skip]
    return grounded_result(raw,tools),storage,case_id


# 未查规则不等于资料缺失，且规则和能力状态不混入客户补证清单。
def test_unqueried_and_missing_sources_are_distinct(tmp_path):
    answer,_,_=analyze(tmp_path,9,skip=('get_policy',))
    gap=next(g for g in answer['information_gaps'] if g['code']=='get_policy')
    assert gap['category']=='not_queried'
    assert '缺少适用规则' not in answer['recommendation']
    answer,_,_=analyze(tmp_path,9)
    gap=next(g for g in answer['information_gaps'] if g['code']=='get_policy')
    assert gap['category']=='source_missing'
    assert not any('规则' in s for s in answer['customer_requests'])


# 未发货查无物流是正常阶段，不让客户提供不存在的运单。
def test_unshipped_absence_does_not_request_tracking_from_customer(tmp_path):
    answer,_,_=analyze(tmp_path,5)
    assert any(g['category']=='normal_absence' and g['code']=='get_shipments' for g in answer['information_gaps'])
    assert not any('运单' in s for s in answer['customer_requests'])
    assert any(g['code']=='fulfillment_records' for g in answer['development_gaps'])


# 确实缺照片才索取，已有附件只登记识图能力未接入。
def test_photo_request_and_image_capability_are_separate(tmp_path):
    answer,_,_=analyze(tmp_path,3)
    assert any('照片' in s for s in answer['customer_requests'])
    assert not any(g['code']=='image_review' for g in answer['development_gaps'])
    answer,_,_=analyze(tmp_path,8)
    assert not any('照片' in s for s in answer['customer_requests'])
    assert any(g['code']=='image_review' for g in answer['development_gaps'])


# 到账查询能力缺口与实际退款记录分开，不要求补无关规则。
def test_refund_status_does_not_require_irrelevant_policy(tmp_path):
    answer,_,_=analyze(tmp_path,11,skip=('get_policy','get_shipments'))
    assert not any(g['code'] in ('get_policy','get_shipments') for g in answer['information_gaps'])
    assert any(g['code']=='payment_settlement' for g in answer['development_gaps'])
    assert answer['customer_requests']==[]
    assert answer['facts']['refund_confirmed'] is True


# 账实矛盾归为客服核对，分析仍完成并保留人工权限边界。
def test_conflicting_refund_is_a_data_review_not_customer_evidence(tmp_path):
    answer,_,_=analyze(tmp_path,12)
    assert any(g['code']=='refund_evidence' and g['category']=='data_conflict' for g in answer['information_gaps'])
    assert answer['customer_requests']==[]
    assert answer['review_items']


# 已知截止时间但物流证据不足，不重复向客户询问已明确的退款条件。
def test_known_condition_missing_tracking_is_an_internal_gap(tmp_path):
    _,case,expected=next(c for c in evaluation_cases() if c[0]=='condition-missing-proof')
    storage=Storage(tmp_path/'condition.db'); case_id=storage.save_case(case); tools=CaseTools(storage,case_id)
    raw=offline_analyzer(expected)(case_id,'run',tools)
    answer=grounded_result(raw,tools)
    assert any(g['code']=='condition_evidence' for g in answer['information_gaps'])
    assert not any('条件' in s for s in answer['customer_requests'])


# 新页面将客户补证与资料能力状态分开，保留旧结果的展示兼容。
def test_page_separates_gaps_and_keeps_legacy_results(tmp_path):
    answer,storage,case_id=analyze(tmp_path,3)
    run_id=storage.create_run(case_id); storage.set_run(run_id,'succeeded',answer)
    with TestClient(create_app(storage.path)) as client:
        page=client.get('/runs/'+run_id).text
        assert '客户需补充' in page and '资料与能力状态' in page
        assert '待确认信息' not in page
    legacy=dict(answer)
    for key in ('gap_schema_version','information_gaps','customer_requests','development_gaps'):
        legacy.pop(key)
    storage.set_run(run_id,'succeeded',legacy)
    with TestClient(create_app(storage.path)) as client:
        page=client.get('/runs/'+run_id).text
        assert '待确认信息' in page


# 非法历史资料结构单独登记，不能因新增分类使本来可保留的分析崩溃。
def test_bad_history_format_becomes_technical_gap(tmp_path):
    _,case,expected=evaluation_cases('independent')[11]
    storage=Storage(tmp_path/'format.db'); case_id=storage.save_case(case); tools=CaseTools(storage,case_id)
    raw=offline_analyzer(expected)(case_id,'run',tools)
    tools.observed_results['get_history']['data']=['unsupported']
    answer=grounded_result(raw,tools)
    assert any(g['code']=='history_format' and g['category']=='technical_error' for g in answer['information_gaps'])
    assert not any('格式' in s for s in answer['customer_requests'])
