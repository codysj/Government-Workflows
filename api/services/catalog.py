"""Workflow catalog: map registry descriptors -> contract WorkflowInfo."""
from __future__ import annotations

from typing import Optional

from api.schemas.models import TextInputInfo, UploadFieldInfo, WorkflowInfo
from src.workflows.registry import WorkflowDescriptor, get_descriptor, list_descriptors


def workflow_info(d: WorkflowDescriptor) -> WorkflowInfo:
    return WorkflowInfo(
        workflow_type=d.workflow_type,
        title=d.title,
        description=d.description,
        note=d.note or None,
        category=d.category,
        uploads=[
            UploadFieldInfo(key=u.key, label=u.label, required=u.required,
                            file_types=list(u.file_types), help=u.help)
            for u in d.uploads
        ],
        text_inputs=[
            TextInputInfo(key=t.key, label=t.label, required=t.required,
                          help=t.help, example=t.example)
            for t in d.text_inputs
        ],
        has_sample=d.has_sample,
        sample_description=d.sample_description,
    )


def list_workflow_infos() -> list[WorkflowInfo]:
    return [workflow_info(d) for d in list_descriptors()]


def get_workflow_info(workflow_type: str) -> Optional[WorkflowInfo]:
    try:
        return workflow_info(get_descriptor(workflow_type))
    except KeyError:
        return None
