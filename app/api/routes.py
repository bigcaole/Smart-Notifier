from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.core.security import verify_basic_auth
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate
from app.services.scheduler_service import scheduler_service
from app.services.task_service import TaskService

router = APIRouter(prefix="/api", tags=["tasks"], dependencies=[Depends(verify_basic_auth)])


class TaskStatusUpdate(BaseModel):
    status: str


def _present(task):
    task.trigger_time = TaskService.to_local_display(task.trigger_time)
    return task


@router.get("/tasks", response_model=list[TaskRead])
async def list_tasks(chat_id: str | None = None, status: str | None = None, db: AsyncSession = Depends(get_db)):
    if status and status not in {"pending", "completed"}:
        raise HTTPException(status_code=400, detail="status must be pending or completed")
    tasks = await TaskService.list_tasks(db, status=status, chat_id=chat_id)
    return [_present(t) for t in tasks]


@router.post("/tasks", response_model=TaskRead)
async def create_task(payload: TaskCreate, db: AsyncSession = Depends(get_db)):
    task = await TaskService.create_task(db, payload)
    scheduler_service.schedule_task(task)
    return _present(task)


@router.post("/tasks/{task_id}/done", response_model=TaskRead)
async def done_task(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await TaskService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await TaskService.mark_done(db, task)
    scheduler_service.remove_task_job(task.id)
    return _present(task)


@router.put("/tasks/{task_id}/status", response_model=TaskRead)
async def update_task_status(task_id: int, payload: TaskStatusUpdate, db: AsyncSession = Depends(get_db)):
    if payload.status not in {"pending", "completed"}:
        raise HTTPException(status_code=400, detail="status must be pending or completed")

    task = await TaskService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    task = await TaskService.update_status(db, task, payload.status)
    if payload.status == "completed":
        scheduler_service.remove_task_job(task.id)
    else:
        scheduler_service.schedule_task(task)
    return _present(task)


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: int, db: AsyncSession = Depends(get_db)):
    task = await TaskService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await TaskService.delete_task(db, task)
    scheduler_service.remove_task_job(task_id)
    return {"ok": True}


@router.put("/tasks/{task_id}", response_model=TaskRead)
async def update_task(task_id: int, payload: TaskUpdate, db: AsyncSession = Depends(get_db)):
    task = await TaskService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if payload.status and payload.status not in {"pending", "completed"}:
        raise HTTPException(status_code=400, detail="status must be pending or completed")

    updated = await TaskService.update_task_fields(
        db,
        task,
        content=payload.content,
        remarks=payload.remarks,
        trigger_time=payload.trigger_time,
        cron_expr=payload.cron_expr,
        status=payload.status,
    )
    if updated.status == "pending":
        scheduler_service.schedule_task(updated)
    else:
        scheduler_service.remove_task_job(updated.id)
    return _present(updated)
