#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Async lazy loader for QSDsan components.

This module provides non-blocking, thread-safe loading of QSDsan components
to prevent MCP event loop blocking during the 18-second import.

Key features:
- First call triggers async load in background thread
- All concurrent calls await the same load task
- Components are cached globally after first load
- Background warmup can be started after server startup
- Event loop remains responsive during 18s import

Supports both mADM1 (63 components) and ASM2d (17 components) models.
"""
import asyncio
import time
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Global cache and synchronization
_components_cache: Dict[str, Any] = {}
_load_tasks: Dict[str, asyncio.Task] = {}


async def _do_load_madm1():
    """
    Load mADM1 components in a background thread.

    Returns:
        QSDsan Components object for mADM1 (63 components)
    """
    def _import_madm1():
        """Synchronous import work (runs in thread pool)."""
        logger.info("Starting mADM1 component creation in background thread...")
        import_start = time.time()

        # Import from the models module (will be created in Phase 1B)
        try:
            from models.madm1 import create_madm1_cmps
            components = create_madm1_cmps()
        except ImportError:
            # Fallback for development - return None to indicate not yet implemented
            logger.warning("mADM1 model not yet implemented, returning None")
            return None

        import_elapsed = time.time() - import_start
        logger.info(f"mADM1 component creation completed in {import_elapsed:.1f}s")
        return components

    start_time = time.time()
    logger.info("Offloading mADM1 import to AnyIO thread pool...")

    import anyio
    components = await anyio.to_thread.run_sync(
        _import_madm1,
        limiter=anyio.to_thread.current_default_thread_limiter()
    )

    total_elapsed = time.time() - start_time
    logger.info(f"Total mADM1 async load time: {total_elapsed:.1f}s")

    return components


async def _do_load_asm2d():
    """
    Load ASM2d components in a background thread.

    Returns:
        QSDsan Components object for ASM2d (17 components)
    """
    def _import_asm2d():
        """Synchronous import work (runs in thread pool)."""
        logger.info("Starting ASM2d component creation in background thread...")
        import_start = time.time()

        try:
            from models.asm2d import create_asm2d_components
            components = create_asm2d_components()
        except ImportError:
            logger.warning("ASM2d model not yet implemented, returning None")
            return None

        import_elapsed = time.time() - import_start
        logger.info(f"ASM2d component creation completed in {import_elapsed:.1f}s")
        return components

    start_time = time.time()
    logger.info("Offloading ASM2d import to AnyIO thread pool...")

    import anyio
    components = await anyio.to_thread.run_sync(
        _import_asm2d,
        limiter=anyio.to_thread.current_default_thread_limiter()
    )

    total_elapsed = time.time() - start_time
    logger.info(f"Total ASM2d async load time: {total_elapsed:.1f}s")

    return components


async def get_components(model_type: str):
    """
    Get QSDsan components for a model type, loading asynchronously if not cached.

    Thread-safe: Multiple concurrent calls will await the same load task.

    Args:
        model_type: Model type string ("mADM1", "ASM2d", etc.)

    Returns:
        QSDsan Components object for the requested model

    Example:
        >>> components = await get_components("mADM1")
    """
    global _components_cache, _load_tasks

    model_key = model_type.lower()

    # Fast path: already loaded
    if model_key in _components_cache:
        logger.debug(f"{model_type} components retrieved from cache")
        return _components_cache[model_key]

    # Slow path: need to load
    if model_key not in _load_tasks:
        logger.info(f"First caller - creating {model_type} load task")
        if model_key in ("madm1", "mADM1"):
            _load_tasks[model_key] = asyncio.create_task(_do_load_madm1())
        elif model_key in ("asm2d", "ASM2d", "masm2d", "mASM2d"):
            _load_tasks[model_key] = asyncio.create_task(_do_load_asm2d())
        else:
            raise ValueError(f"Unknown model type: {model_type}")
    else:
        logger.info(f"Waiting for ongoing {model_type} load task...")

    _components_cache[model_key] = await _load_tasks[model_key]
    logger.info(f"{model_type} components now cached and ready")
    return _components_cache[model_key]


def start_background_warmup(model_type: str = "mADM1"):
    """
    Start loading QSDsan components in the background.

    Call this after MCP server startup to pre-warm the cache.

    Args:
        model_type: Model type to pre-load ("mADM1", "ASM2d")

    Example:
        >>> from utils.qsdsan_loader import start_background_warmup
        >>> asyncio.create_task(start_background_warmup("mADM1"))
    """
    logger.info(f"Starting background {model_type} warmup task...")

    async def _warmup():
        try:
            await get_components(model_type)
            logger.info(f"Background {model_type} warmup completed successfully")
        except Exception as e:
            logger.error(f"Background {model_type} warmup failed: {e}", exc_info=True)

    asyncio.create_task(_warmup())


def is_loaded(model_type: str) -> bool:
    """
    Check if components for a model type are already loaded.

    Args:
        model_type: Model type string

    Returns:
        True if components are cached, False otherwise
    """
    return model_type.lower() in _components_cache


async def wait_for_load(model_type: str, timeout: float = 30.0) -> bool:
    """
    Wait for components to load (if loading is in progress).

    Args:
        model_type: Model type string
        timeout: Maximum seconds to wait (default 30s)

    Returns:
        True if loaded within timeout, False if timeout occurred
    """
    model_key = model_type.lower()

    if model_key in _components_cache:
        return True

    if model_key not in _load_tasks:
        return False

    try:
        await asyncio.wait_for(_load_tasks[model_key], timeout=timeout)
        return model_key in _components_cache
    except asyncio.TimeoutError:
        logger.warning(f"{model_type} load did not complete within {timeout}s")
        return False


def clear_cache():
    """Clear all cached components. Useful for testing."""
    global _components_cache, _load_tasks
    _components_cache.clear()
    _load_tasks.clear()
    logger.info("Component cache cleared")
