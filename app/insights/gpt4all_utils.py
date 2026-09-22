"""Utilities for managing GPT4All Phi-2 model availability and caching."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def is_model_cached() -> bool:
    """Check if Phi-2 model is already downloaded and cached.

    Checks ~/.cache/gpt4all/ for the Phi-2 model file.

    Returns:
        True if model is cached locally, False otherwise.
    """
    try:
        from gpt4all import GPT4All

        # GPT4All caches models to ~/.cache/gpt4all/
        # Check if model is available without downloading
        available = GPT4All.is_model_available("phi-2")
        logger.debug(f"Model cache check: available={available}")
        return available
    except Exception as e:
        logger.warning(f"Failed to check model cache: {e}")
        return False


def get_cache_directory() -> Path:
    """Get the GPT4All model cache directory.

    Returns:
        Path to ~/.cache/gpt4all/ or fallback cache location.
    """
    try:
        from gpt4all import GPT4All

        # GPT4All provides the default download directory
        cache_dir = Path.home() / ".cache" / "gpt4all"
        return cache_dir
    except Exception:
        # Fallback to standard cache location
        return Path.home() / ".cache" / "gpt4all"


def get_model_info() -> dict[str, str | int | float]:
    """Get information about the Phi-2 model.

    Returns:
        Dictionary with model metadata:
        - name: "phi-2"
        - size_mb: 1600 (approximate disk size)
        - ram_mb: 4096-6144 (estimated RAM needed during inference)
        - description: Human-readable description
    """
    return {
        "name": "phi-2",
        "size_mb": 1600,
        "ram_mb_min": 4096,
        "ram_mb_max": 6144,
        "description": "Phi-2 2.7B lightweight LLM optimized for ADF error analysis",
    }


def ensure_model_available() -> bool:
    """Ensure the Phi-2 model is available, downloading if necessary.

    This function will:
    1. Check if model is cached
    2. If not, attempt to download it
    3. Return success/failure status

    Returns:
        True if model is now available, False if download failed.

    Note:
        First call may take ~1 minute if model needs downloading.
        Subsequent calls will use cached model.
    """
    try:
        if is_model_cached():
            logger.debug("Phi-2 model already cached")
            return True

        logger.info("Phi-2 model not cached, downloading...")
        from gpt4all import GPT4All

        # This will download to ~/.cache/gpt4all/
        # Takes ~1 minute on typical internet connection
        model = GPT4All("phi-2")
        logger.info("Phi-2 model downloaded and cached successfully")
        return True

    except ImportError:
        logger.error("GPT4All not installed. Install with: pip install gpt4all")
        return False
    except Exception as e:
        logger.error(f"Failed to ensure model availability: {e}")
        return False


def clear_cache() -> bool:
    """Clear the GPT4All model cache.

    WARNING: This will delete the downloaded model file (~1.6 GB).
    Model will need to be re-downloaded on next use.

    Returns:
        True if cache was cleared, False if error occurred.
    """
    try:
        import shutil

        cache_dir = get_cache_directory()
        if cache_dir.exists():
            logger.warning(f"Clearing GPT4All cache: {cache_dir}")
            shutil.rmtree(cache_dir)
            logger.info("Cache cleared successfully")
            return True
        else:
            logger.debug("Cache directory does not exist")
            return True

    except Exception as e:
        logger.error(f"Failed to clear cache: {e}")
        return False
