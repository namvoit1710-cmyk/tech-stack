import math
import os
import pathlib
from typing import Any, Optional

import psutil

from app.layer4_frameworks.config.app_config import settings


_last_resolved_batch_size: Optional[int] = None
_last_estimated_bytes_per_row: Optional[int] = None


def _get_inactive_file_memory() -> int:
    """Parse inactive_file from memory.stat (v1 or v2)."""
    try:
        paths = [
            pathlib.Path("/sys/fs/cgroup/memory.stat"),          # v2
            pathlib.Path("/sys/fs/cgroup/memory/memory.stat")    # v1
        ]
        for stat_path in paths:
            if stat_path.exists():
                for line in stat_path.read_text().splitlines():
                    parts = line.strip().split()
                    # v2 is inactive_file, v1 is total_inactive_file
                    if len(parts) == 2 and parts[0] in ("inactive_file", "total_inactive_file"):
                        return int(parts[1])
                break
    except Exception:
        pass
    return 0


def _get_container_memory() -> Optional[dict]:
    """Detect container memory limits via cgroup v1 or v2.
    Returns dict with total, available, available_percent or None if not in container.
    """
    total = None
    current = None
    
    try:
        # Check cgroup v2
        memory_max = pathlib.Path("/sys/fs/cgroup/memory.max")
        memory_current = pathlib.Path("/sys/fs/cgroup/memory.current")
        
        # Check cgroup v1
        memory_limit = pathlib.Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
        memory_usage = pathlib.Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")
        
        if memory_max.exists() and memory_current.exists():
            max_str = memory_max.read_text().strip()
            if max_str != "max":
                total = int(max_str)
            current = int(memory_current.read_text().strip())
        elif memory_limit.exists() and memory_usage.exists():
            total = int(memory_limit.read_text().strip())
            # cgroup v1 sets limit to 9223372036854771712 if no limit is applied
            if total > 1024**5: 
                total = None
            current = int(memory_usage.read_text().strip())

        if total is None or current is None:
            return None

        inactive_file = _get_inactive_file_memory()
        
        # Subtract inactive_file (page cache) since it is reclaimable by the kernel
        current_adjusted = max(0, current - inactive_file)
        available = max(0, total - current_adjusted)
        available_percent = (available / total) * 100 if total else 100.0
        return {
            "total": total,
            "current": current_adjusted,
            "available": available,
            "available_percent": available_percent,
            "available_mb": available / (1024 * 1024),
        }
    except (OSError, ValueError):
        return None


def _get_system_memory() -> dict:
    """Get system memory info — checks cgroup v2 first, falls back to psutil."""
    container_mem = _get_container_memory()
    if container_mem is not None:
        return container_mem

    vm = psutil.virtual_memory()
    available_percent = (vm.available / vm.total) * 100 if vm.total else 100.0
    return {
        "total": vm.total,
        "current": vm.total - vm.available,
        "available": vm.available,
        "available_percent": available_percent,
        "available_mb": vm.available / (1024 * 1024),
    }


def resolve_adaptive_batch_size(
    df: Any,
    total_rows: int,
    logger: Any,
    *,
    workload: str,
) -> int:
    global _last_resolved_batch_size
    configured_batch_size = max(1, settings.BATCH_SIZE)
    if total_rows <= 0:
        return configured_batch_size

    clamped_min = max(1, settings.BATCH_SIZE_MIN)
    clamped_max = max(clamped_min, settings.BATCH_SIZE_MAX)

    if not settings.ADAPTIVE_BATCHING_ENABLED:
        return min(total_rows, max(clamped_min, min(configured_batch_size, clamped_max)))

    # Compute memory-based batch size
    try:
        mem_info = _get_system_memory()
        available_bytes = mem_info["available"]
        bytes_per_row = _estimate_bytes_per_row(df)
        target_bytes = max(1, int(available_bytes * settings.BATCH_MEMORY_FRACTION))
        target_rows = max(
            1,
            int(target_bytes / max(1, math.ceil(bytes_per_row * settings.BATCH_WORKING_SET_MULTIPLIER))),
        )
        resolved = max(clamped_min, min(target_rows, clamped_max, total_rows))
        logger.info(
            "Resolved adaptive batch size",
            workload=workload,
            batch_size=resolved,
            available_memory_mb=round(available_bytes / (1024 * 1024), 2),
            bytes_per_row=bytes_per_row,
        )
        # VERY IMPORTANT: Hard cap to 25,000 to prevent OOM spikes during heavy Regex
        resolved = min(resolved, 25000)
        _last_resolved_batch_size = resolved
        return resolved
    except Exception as exc:
        logger.warning(
            "Falling back to configured batch size",
            workload=workload,
            reason=str(exc),
        )
        resolved = min(total_rows, max(clamped_min, min(configured_batch_size, clamped_max)))
        _last_resolved_batch_size = resolved
        return resolved


def resolve_runtime_batch_size(
    df: Any,
    remaining_rows: int,
    current_batch_size: int,
    logger: Any,
    *,
    workload: str,
) -> int:
    global _last_resolved_batch_size
    safe_current = current_batch_size if isinstance(current_batch_size, int) and current_batch_size > 0 else 1
    if remaining_rows <= 0:
        return max(1, safe_current)

    suggested = resolve_adaptive_batch_size(
        df,
        remaining_rows,
        logger,
        workload=workload,
    )
    if not isinstance(suggested, int) or suggested <= 0:
        suggested = safe_current

    if not settings.ADAPTIVE_BATCH_RUNTIME_SHRINK_ENABLED:
        return max(1, min(safe_current, remaining_rows))

    if settings.ADAPTIVE_BATCH_ALLOW_GROWTH:
        updated = suggested
    else:
        updated = min(safe_current, suggested)

    updated = max(1, min(updated, remaining_rows))
    if updated < safe_current:
        logger.warning(
            "Shrinking batch size due to memory pressure",
            workload=workload,
            previous_batch_size=safe_current,
            new_batch_size=updated,
            remaining_rows=remaining_rows,
        )
    _last_resolved_batch_size = updated
    return updated


def compute_current_batch_size(
    df: Optional[Any] = None,
    total_rows: int = 1_000_000,
    logger: Optional[Any] = None,
    *,
    workload: str = "health_check",
) -> int:
    """Compute what batch size would be given current memory, without side effects.
    Used by /health endpoint to expose adaptive batch size under current memory pressure.
    """
    configured_batch_size = max(1, settings.BATCH_SIZE)
    clamped_min = max(1, settings.BATCH_SIZE_MIN)
    clamped_max = max(clamped_min, settings.BATCH_SIZE_MAX)

    if not settings.ADAPTIVE_BATCHING_ENABLED:
        return min(total_rows, max(clamped_min, min(configured_batch_size, clamped_max)))

    try:
        mem_info = _get_system_memory()
        available_bytes = mem_info["available"]
        # Use fallback estimate when no real DataFrame is available
        if df is None:
            if _last_estimated_bytes_per_row is not None:
                bytes_per_row = _last_estimated_bytes_per_row
            else:
                bytes_per_row = max(settings.BATCH_FALLBACK_BYTES_PER_ROW, settings.BATCH_FALLBACK_BYTES_PER_COLUMN * 10)
        else:
            bytes_per_row = _estimate_bytes_per_row(df)
        target_bytes = max(1, int(available_bytes * settings.BATCH_MEMORY_FRACTION))
        target_rows = max(
            1,
            int(target_bytes / max(1, math.ceil(bytes_per_row * settings.BATCH_WORKING_SET_MULTIPLIER))),
        )
        resolved = max(clamped_min, min(target_rows, clamped_max, total_rows))
        return min(resolved, 25000)
    except Exception:
        return max(clamped_min, min(configured_batch_size, clamped_max))


def _estimate_bytes_per_row(df: Any) -> int:
    global _last_estimated_bytes_per_row
    row_count = max(1, len(df))
    try:
        estimated_size = int(df.estimated_size())
    except Exception:
        estimated_size = 0

    if estimated_size > 0:
        val = max(1, math.ceil(estimated_size / row_count))
        _last_estimated_bytes_per_row = val
        return val

    column_count = len(getattr(df, "columns", []) or [])
    fallback = max(1, column_count) * max(1, settings.BATCH_FALLBACK_BYTES_PER_COLUMN)
    val = max(settings.BATCH_FALLBACK_BYTES_PER_ROW, fallback)
    _last_estimated_bytes_per_row = val
    return val
