"""FastAPI upload and persistent result view."""
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from agents.ticket_agent import analyze_ticket
from app.observability import RunLogger
from app.storage import Storage
from context.case_schema import CaseValidationError, MAX_BYTES, parse_case
from workflows.analyze_ticket import process_run

ROOT = Path(__file__).resolve().parents[1]
templates = Jinja2Templates(directory=str(ROOT / 'app' / 'templates'))


# 组装应用；默认数据库仅在服务器启动时初始化，避免导入时中断用户任务。
def create_app(db_path=None, analyzer=None):
    storage = Storage(db_path) if db_path else None
    logger = RunLogger(Path(db_path).parent / 'agent.jsonl') if db_path else None

    # 启动服务时恢复运行状态；测试显式传入隔离数据库。
    @asynccontextmanager
    async def lifespan(app):
        nonlocal storage, logger
        if storage is None:
            storage = Storage(ROOT / 'runtime' / 'app.db')
            logger = RunLogger(ROOT / 'runtime' / 'agent.jsonl')
        yield

    app = FastAPI(title='清洁日用品工单 Agent 1.0', lifespan=lifespan)
    analyzer = analyzer or analyze_ticket

    @app.get('/', response_class=HTMLResponse)
    def index(request: Request):
        """展示单案例 JSON 上传页面。"""
        return templates.TemplateResponse(request, 'index.html', {})

    @app.post('/cases/upload')
    async def upload(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
        """校验并保存上传案例，然后排队执行分析。"""
        raw = await file.read(MAX_BYTES + 1)
        try:
            bundle = parse_case(raw)
        except CaseValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        case_id = storage.save_case(bundle)
        run_id = storage.create_run(case_id)
        logger.event(storage, run_id, 'run_queued', '案例校验并保存')
        background_tasks.add_task(process_run, storage, run_id, analyzer, logger)
        return RedirectResponse(f'/runs/{run_id}', status_code=303)

    # 展示最新运行状态及结果，禁用缓存以避免自动刷新读到旧状态。
    @app.get('/runs/{run_id}', response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        try:
            run = storage.get_run(run_id)
            case = storage.get_case(run['case_id'])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='未找到运行记录') from exc
        elapsed_ms = None
        if run['finished_at']:
            elapsed_ms = round((datetime.fromisoformat(run['finished_at']) -
                                datetime.fromisoformat(run['created_at'])).total_seconds() * 1000)
        status_labels = {'queued': '等待分析', 'running': '分析中', 'succeeded': '分析完成',
                         'needs_information': '待补充信息', 'failed': '分析失败', 'interrupted': '分析中断'}
        if run['result'] and run['result'].get('review_required'):
            status_labels['needs_information'] = '待人工复核'
        tool_labels = {'get_ticket': '工单', 'get_order': '订单', 'get_product': '商品',
                       'get_shipments': '物流', 'get_history': '历史记录', 'get_policy': '店铺规则'}
        event_labels = []
        for event in run['events']:
            if event['event_type'] == 'tool_call':
                name, _, status = event['summary'].partition(':')
                event_labels.append(f'{tool_labels.get(name, "资料")}查询' +
                                    {'found': '成功', 'not_found': '无记录', 'forbidden': '未获授权'}.get(status, '失败'))
            elif event['event_type'] == 'run_queued':
                event_labels.append('案例已接收')
            elif event['event_type'] == 'run_started':
                event_labels.append('开始分析')
            elif event['event_type'] == 'run_finished':
                event_labels.append('分析结束')
        return templates.TemplateResponse(request, 'result.html',
                                          {'run': run, 'case': case, 'elapsed_ms': elapsed_ms,
                                           'status_label': status_labels.get(run['status'], '未知状态'),
                                           'event_labels': event_labels},
                                          headers={'Cache-Control': 'no-store'})

    @app.get('/health')
    def health():
        """检查服务和 SQLite 数据库是否可用。"""
        try:
            with storage._db() as db:
                db.execute('SELECT 1')
        except Exception as exc:
            raise HTTPException(status_code=503, detail='数据库不可用') from exc
        return {'status': 'ok'}

    return app


app = create_app()
