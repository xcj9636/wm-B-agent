"""
工作流相关API
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from uuid import UUID

from app.db import get_db
from app.models import database as models
from app.models.schemas import (
    WorkflowCreate, WorkflowUpdate, WorkflowResponse, WorkflowExecuteRequest,
    ExecutionResponse, ExecutionInterruptRequest
)
from app.core.agent import get_agent
from app.core.workflow_engine import WorkflowDefinition
from app.api.v1.auth import get_current_active_user

router = APIRouter()


def _build_definition(
    *,
    name: str,
    description: Optional[str],
    version: str,
    steps: List[dict],
    transitions: List[dict],
    variables: dict,
) -> WorkflowDefinition:
    """Build the engine definition through its canonical deserializer."""
    return WorkflowDefinition.from_dict(
        {
            "name": name,
            "description": description or "",
            "version": version,
            "steps": steps,
            "transitions": transitions,
            "variables": variables,
        }
    )


@router.get("", response_model=List[WorkflowResponse])
async def list_workflows(
    status: Optional[str] = None,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """列出所有工作流"""
    query = db.query(models.Workflow).filter(
        models.Workflow.user_id == current_user.id
    )

    if status:
        query = query.filter(models.Workflow.status == status)

    workflows = query.all()
    return workflows


@router.post("", response_model=WorkflowResponse)
async def create_workflow(
    workflow: WorkflowCreate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """创建工作流"""
    definition = _build_definition(
        name=workflow.name,
        description=workflow.description,
        version=workflow.version,
        steps=[step.model_dump() for step in workflow.steps],
        transitions=[transition.model_dump() for transition in workflow.transitions],
        variables=workflow.variables,
    )

    # Create database record
    db_workflow = models.Workflow(
        name=workflow.name,
        description=workflow.description,
        status=models.WorkflowStatus.DRAFT,
        config_json=definition.to_dict(),
        variables=workflow.variables,
        tags=workflow.tags,
        user_id=current_user.id,
        version=workflow.version,
    )
    db.add(db_workflow)
    db.commit()
    db.refresh(db_workflow)

    # Register in agent
    agent = get_agent()
    agent.register_workflow(definition)

    return db_workflow


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: UUID,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """获取工作流详情"""
    workflow = db.query(models.Workflow).filter(
        models.Workflow.id == workflow_id,
        models.Workflow.user_id == current_user.id
    ).first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    return workflow


@router.put("/{workflow_id}", response_model=WorkflowResponse)
async def update_workflow(
    workflow_id: UUID,
    workflow_update: WorkflowUpdate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """更新工作流"""
    workflow = db.query(models.Workflow).filter(
        models.Workflow.id == workflow_id,
        models.Workflow.user_id == current_user.id
    ).first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    update_data = workflow_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        if key in ["steps", "transitions", "variables"]:
            continue
        if key == "status" and value is not None:
            value = models.WorkflowStatus(value.value)
        setattr(workflow, key, value)

    current_config = workflow.config_json or {}
    variables = update_data.get(
        "variables", workflow.variables or current_config.get("variables", {})
    )
    definition = _build_definition(
        name=workflow.name,
        description=workflow.description,
        version=workflow.version,
        steps=update_data.get("steps", current_config.get("steps", [])),
        transitions=update_data.get(
            "transitions", current_config.get("transitions", [])
        ),
        variables=variables,
    )
    workflow.variables = variables
    workflow.config_json = definition.to_dict()

    db.commit()
    db.refresh(workflow)

    # Update in agent
    agent = get_agent()
    agent.register_workflow(definition)

    return workflow


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: UUID,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """删除工作流"""
    workflow = db.query(models.Workflow).filter(
        models.Workflow.id == workflow_id,
        models.Workflow.user_id == current_user.id
    ).first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    db.delete(workflow)
    db.commit()

    return {"message": "Workflow deleted"}


@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: UUID,
    request: WorkflowExecuteRequest,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """执行工作流"""
    workflow = db.query(models.Workflow).filter(
        models.Workflow.id == workflow_id,
        models.Workflow.user_id == current_user.id
    ).first()

    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")

    agent = get_agent()
    definition = WorkflowDefinition.from_dict(workflow.config_json)
    agent.register_workflow(definition)

    async def execution_generator():
        import json

        execution_id = None
        results = []

        try:
            async for update in agent.execute_workflow(
                workflow.name,
                request.input_data,
                current_user.id
            ):
                if update.get("type") == "started":
                    execution_id = update.get("execution_id")

                results.append(update)

                # For SSE-like response
                yield f"data: {json.dumps(update)}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        execution_generator(),
        media_type="text/event-stream"
    )


@router.get("/executions/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: str,
    current_user: models.User = Depends(get_current_active_user)
):
    """获取执行状态"""
    agent = get_agent()
    execution = agent.get_execution_status(execution_id)

    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")

    return execution


@router.post("/executions/{execution_id}/interrupt")
async def interrupt_execution(
    execution_id: str,
    request: ExecutionInterruptRequest,
    current_user: models.User = Depends(get_current_active_user)
):
    """中断/接管执行"""
    agent = get_agent()

    result = await agent.handle_interrupt(
        execution_id,
        request.action,
        request.data
    )

    return result


@router.post("/executions/{execution_id}/pause")
async def pause_execution(
    execution_id: str,
    current_user: models.User = Depends(get_current_active_user)
):
    """暂停执行"""
    agent = get_agent()
    agent.pause_execution(execution_id)

    return {"message": "Execution paused"}


@router.post("/executions/{execution_id}/resume")
async def resume_execution(
    execution_id: str,
    current_user: models.User = Depends(get_current_active_user)
):
    """恢复执行"""
    agent = get_agent()
    agent.resume_execution(execution_id)

    return {"message": "Execution resumed"}


@router.post("/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: str,
    current_user: models.User = Depends(get_current_active_user)
):
    """取消执行"""
    agent = get_agent()
    agent.cancel_execution(execution_id)

    return {"message": "Execution cancelled"}
