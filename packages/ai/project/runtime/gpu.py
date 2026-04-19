"""GPU lease facade for project workflows."""

from __future__ import annotations

from typing import Any

from packages.ai.project.gpu_lease_service import (
    acquire_gpu_lease,
    list_active_gpu_leases,
    reconcile_gpu_leases,
    release_gpu_lease,
    touch_gpu_lease,
)


def merge_active_gpu_leases(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _merge_active_gpu_leases

    return _merge_active_gpu_leases(*args, **kwargs)


def reconcile_remote_gpu_leases(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _reconcile_remote_gpu_leases

    return _reconcile_remote_gpu_leases(*args, **kwargs)


def select_remote_gpu(*args: Any, **kwargs: Any) -> Any:
    from packages.ai.project.workflow_runner import _select_remote_gpu

    return _select_remote_gpu(*args, **kwargs)


__all__ = [
    "acquire_gpu_lease",
    "list_active_gpu_leases",
    "merge_active_gpu_leases",
    "reconcile_gpu_leases",
    "reconcile_remote_gpu_leases",
    "release_gpu_lease",
    "select_remote_gpu",
    "touch_gpu_lease",
]

