"""Build workflow ``inputs`` dicts from multipart form data.

Mirrors what the Streamlit run page and the CLI do: uploaded files are saved
under ``upload_dir/<unique id>/<original name>`` and their PATHS are passed to
the core; ``use_sample=true`` resolves the bundled synthetic example files.
No financial logic lives here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from starlette.datastructures import FormData, UploadFile

from app import workflow_registry as wfr
from api.services.catalog import TEXT_INPUTS_BY_TYPE

_TRUE_STRINGS = {"true", "1", "yes", "on"}

# Freeform's boolean confirmation fields (sent as text "true"/"false").
_FREEFORM_BOOL_KEYS = ("sensitivity_confirmation", "human_review_confirmation")


class InputError(Exception):
    """A user-correctable input problem -> HTTP 422 with a plain message."""


def parse_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_STRINGS


def parse_config_field(form: FormData) -> Optional[dict]:
    """Parse the optional ``config`` JSON-string form field."""
    raw = form.get("config")
    if raw is None or isinstance(raw, UploadFile) or not str(raw).strip():
        return None
    try:
        cfg = json.loads(str(raw))
    except json.JSONDecodeError:
        raise InputError(
            "The 'config' field must be a valid JSON object string.")
    if not isinstance(cfg, dict):
        raise InputError("The 'config' field must be a JSON object.")
    return cfg


def _save_upload(upload: UploadFile, dest_dir: Path) -> str:
    """Save one uploaded file and return its absolute path string."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Use only the basename so a hostile filename cannot escape dest_dir.
    name = Path(upload.filename or "upload.bin").name or "upload.bin"
    dest = dest_dir / name
    with dest.open("wb") as fh:
        upload.file.seek(0)
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            fh.write(chunk)
    return str(dest)


def build_inputs(
    workflow_type: str,
    form: FormData,
    upload_dir: Path,
) -> tuple[dict[str, Any], bool]:
    """Build the core ``inputs`` dict from the multipart form.

    Returns ``(inputs, use_sample)``. Uploaded files override sample files for
    the same key. Raises ``KeyError`` for unknown workflow types (callers map
    that to 404) and ``InputError`` for malformed fields (-> 422).
    """
    descriptor = wfr.get_descriptor(workflow_type)  # KeyError -> 404 upstream
    use_sample = parse_bool(form.get("use_sample"))
    inputs: dict[str, Any] = {}

    # 1. Sample files (bundled synthetic data).
    if use_sample:
        for key, rel in descriptor.example_files.items():
            inputs[key] = str(wfr.example_path(rel))
        if workflow_type == "transaction_search":
            inputs["query"] = wfr.TRANSACTION_SEARCH_EXAMPLE_QUERY

    # 2. Uploaded files (one part per upload key; override samples).
    run_scope = upload_dir / wfr.make_id_safe()
    uploaded_any = False
    for field in descriptor.uploads:
        if field.key == "uploaded_files":
            # Freeform: zero or more supporting files under one key.
            files = [
                v for v in form.getlist("uploaded_files")
                if isinstance(v, UploadFile) and v.filename
            ]
            inputs["uploaded_files"] = [
                _save_upload(f, run_scope) for f in files
            ]
            uploaded_any = uploaded_any or bool(files)
            continue
        value = form.get(field.key)
        if isinstance(value, UploadFile) and value.filename:
            inputs[field.key] = _save_upload(value, run_scope)
            uploaded_any = True

    # 3. Text inputs (query, freeform structured fields).
    for spec in TEXT_INPUTS_BY_TYPE.get(workflow_type, []):
        value = form.get(spec.key)
        if value is None or isinstance(value, UploadFile):
            continue
        text = str(value).strip()
        if not text:
            continue
        if workflow_type == "freeform" and spec.key in _FREEFORM_BOOL_KEYS:
            inputs[spec.key] = parse_bool(text)
        else:
            inputs[spec.key] = text

    if workflow_type == "freeform":
        # The freeform module expects all structured keys present.
        inputs.setdefault("task_type", "")
        inputs.setdefault("desired_output", "")
        inputs.setdefault("relevant_context", "")
        inputs.setdefault("sensitivity_confirmation", False)
        inputs.setdefault("human_review_confirmation", False)
        inputs.setdefault("uploaded_files", [])

    return inputs, (use_sample or uploaded_any)


def build_sample_inputs(workflow_type: str) -> dict[str, Any]:
    """Build the core ``inputs`` dict from a workflow's BUNDLED sample files.

    This is the no-form analogue of the ``use_sample=true`` branch of
    :func:`build_inputs`, reused by the schedule manual-trigger endpoint so a
    scheduled run goes through the exact same sample resolution as an
    interactive sample run. Raises ``KeyError`` for an unknown workflow type
    (callers map that to 404) and ``InputError`` when the workflow ships no
    sample inputs to run.
    """
    descriptor = wfr.get_descriptor(workflow_type)  # KeyError -> 404 upstream
    inputs: dict[str, Any] = {}
    for key, rel in descriptor.example_files.items():
        inputs[key] = str(wfr.example_path(rel))
    if workflow_type == "transaction_search":
        inputs["query"] = wfr.TRANSACTION_SEARCH_EXAMPLE_QUERY
    if not inputs:
        raise InputError(
            f"Workflow '{workflow_type}' has no bundled sample inputs, so it "
            "cannot be triggered on a schedule without uploaded files.")
    return inputs


def validate_required_inputs(workflow_type: str, inputs: dict[str, Any]) -> None:
    """Plain-language 422s for missing required inputs (run endpoint only).

    Anything subtler (missing columns, at-least-one-data-file rules) is left
    to the deterministic preflight engine, which reports it as a structured
    domain outcome rather than an HTTP error.
    """
    descriptor = wfr.get_descriptor(workflow_type)
    missing_files = [
        f.label for f in descriptor.uploads
        if f.required and f.key != "uploaded_files" and not inputs.get(f.key)
    ]
    if missing_files:
        raise InputError(
            "Missing required file(s): " + ", ".join(missing_files)
            + ". Upload them or set use_sample=true.")

    missing_text = []
    for spec in TEXT_INPUTS_BY_TYPE.get(workflow_type, []):
        if not spec.required or spec.key in _FREEFORM_BOOL_KEYS:
            continue
        val = inputs.get(spec.key)
        if val is None or val == "":
            missing_text.append(spec.label)
    if missing_text:
        raise InputError(
            "Missing required field(s): " + ", ".join(missing_text) + ".")

    if workflow_type == "freeform":
        if inputs.get("sensitivity_confirmation") is not True:
            raise InputError(
                "Set sensitivity_confirmation=true to certify that no real "
                "sensitive data is included. The run is refused otherwise.")
        if inputs.get("human_review_confirmation") is not True:
            raise InputError(
                "Set human_review_confirmation=true to confirm the output "
                "will be human-reviewed before any use.")
