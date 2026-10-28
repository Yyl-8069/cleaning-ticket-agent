"""Minimal structured JSON logging without raw inputs or exception messages."""
import json
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from app.storage import now


class RunLogger:
    # 配置按大小轮转的文件日志和脱敏终端日志。
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(f'ticket.{path}')
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(handler)
            console = logging.StreamHandler(sys.stderr)
            console.setFormatter(logging.Formatter('%(message)s'))
            self.logger.addHandler(console)

    # 将受控事件和指标写入数据库、文件及终端。
    def event(self, storage, run_id, event_type, summary, metrics=None):
        # Only controlled event names and status summaries reach persistent logs.
        storage.event(run_id, event_type, summary)
        safe = {k: v for k, v in (metrics or {}).items()
                if k in ('duration_ms', 'input_tokens', 'output_tokens', 'model')
                and isinstance(v, (str, int, float))}
        self.logger.info(json.dumps({'at': now(), 'run_id': run_id, 'event_type': event_type,
                                     'summary': summary, **safe}, ensure_ascii=False))
