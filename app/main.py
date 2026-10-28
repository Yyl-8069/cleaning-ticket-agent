"""FastAPI upload and persistent result view."""
import os
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


def create_app(db_path=None, analyzer=None):
    app = FastAPI(title='清洁日用品工单 Agent 1.0')
    storage = Storage(db_path or ROOT / 'runtime' / 'app.db')
    logger = RunLogger((Path(db_path).parent if db_path else ROOT / 'runtime') / 'agent.jsonl')
    analyzer = analyzer or analyze_ticket

    @app.get('/', response_class=HTMLResponse)
    def index(request: Request):
        return templates.TemplateResponse(request, 'index.html', {})

    @app.post('/cases/upload')
    async def upload(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
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

    @app.get('/runs/{run_id}', response_class=HTMLResponse)
    def run_page(request: Request, run_id: str):
        try:
            run = storage.get_run(run_id)
            case = storage.get_case(run['case_id'])
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='未找到运行记录') from exc
        return templates.TemplateResponse(request, 'result.html', {'run': run, 'case': case})

    @app.get('/health')
    def health():
        try:
            with storage._db() as db:
                db.execute('SELECT 1')
        except Exception as exc:
            raise HTTPException(status_code=503, detail='数据库不可用') from exc
        return {'status': 'ok'}

    return app


app = create_app()
