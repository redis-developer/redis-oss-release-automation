from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel


class Workflow(BaseModel):
    repo: str
    workflow_file: str
    inputs: Dict[str, str]
    ref: str = "main"
    uuid: Optional[str] = None
    triggered_at: Optional[datetime] = None
    trigger_failed: bool = False
    started_at: Optional[datetime] = None
    run_id: Optional[int] = None
    url: Optional[str] = None
