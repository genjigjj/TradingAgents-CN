from __future__ import annotations

import asyncio
import logging
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
import os

from app.services.config_service import config_service

logger = logging.getLogger(__name__)


class ConfigProvider:
    """Effective configuration provider with simple env→DB merge and TTL cache.

    - Priority: ENV > DB
    - Cache TTL: configurable (default 60s)
    - Invalidate on writes: caller should invoke `invalidate()` after writes

    LLM 配置相关方法已委托给 unified_llm_service，
    本类仅保留非 LLM 相关的系统设置合并逻辑。
    """

    def __init__(self, ttl_seconds: int = 60) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._cache_settings: Optional[Dict[str, Any]] = None
        self._cache_time: Optional[datetime] = None

    def invalidate(self) -> None:
        self._cache_settings = None
        self._cache_time = None

    def _is_cache_valid(self) -> bool:
        return (
            self._cache_settings is not None
            and self._cache_time is not None
            and __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - self._cache_time < self._ttl
        )

    async def get_effective_system_settings(self) -> Dict[str, Any]:
        if self._is_cache_valid():
            return dict(self._cache_settings or {})

        # Load DB settings
        cfg = await config_service.get_system_config()
        base: Dict[str, Any] = {}
        if cfg and getattr(cfg, "system_settings", None):
            try:
                base = dict(cfg.system_settings)
            except Exception:
                base = {}

        # Merge ENV over DB (best-effort heuristics):
        # - if ENV with exact key exists -> override
        # - try uppercased and dot/space to underscore variants
        merged: Dict[str, Any] = dict(base)
        for k, v in list(base.items()):
            candidates = [
                k,
                k.upper(),
                str(k).replace(".", "_").replace(" ", "_").upper(),
            ]
            found = None
            for ek in candidates:
                if ek in os.environ:
                    found = os.environ.get(ek)
                    break
            if found is not None:
                merged[k] = found

        # Optionally: allow whitelisting additional env-only keys via prefix
        # For now, keep minimal behavior to avoid surprising surfaces.

        # Cache
        self._cache_settings = dict(merged)
        self._cache_time = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        return dict(merged)
    async def get_system_settings_meta(self) -> Dict[str, Dict[str, Any]]:
        """Return metadata for system settings keys including sensitivity, editability and source.
        Fields per key:
          - sensitive: bool (by keyword patterns)
          - editable: bool (False if sensitive or source is environment; True otherwise)
          - source: 'environment' | 'database' | 'default'
          - has_value: bool (effective value is not None/empty)
        """
        # Load DB settings raw
        cfg = await config_service.get_system_config()
        db_settings: Dict[str, Any] = {}
        if cfg and getattr(cfg, "system_settings", None):
            try:
                db_settings = dict(cfg.system_settings)
            except Exception:
                db_settings = {}

        def _env_override_for_key(key: str) -> Optional[Any]:
            candidates = [
                key,
                key.upper(),
                str(key).replace(".", "_").replace(" ", "_").upper(),
            ]
            for ek in candidates:
                if ek in os.environ:
                    return os.environ.get(ek)
            return None

        sens_patterns = ("key", "secret", "password", "token", "client_secret")
        meta: Dict[str, Dict[str, Any]] = {}
        for k, v in db_settings.items():
            env_v = _env_override_for_key(k)
            source = "environment" if env_v is not None else ("database" if v is not None else "default")
            sensitive = isinstance(k, str) and any(p in k.lower() for p in sens_patterns)
            editable = not sensitive and source != "environment"
            effective_val = env_v if env_v is not None else v
            has_value = effective_val not in (None, "")
            meta[k] = {
                "sensitive": bool(sensitive),
                "editable": bool(editable),
                "source": source,
                "has_value": bool(has_value),
            }
        return meta

    # === LLM 配置委托方法（委托给 unified_llm_service） ===

    @staticmethod
    def _run_async(coro):
        """
        在同步上下文中运行异步协程。

        优先尝试获取当前运行中的事件循环，
        如果已在事件循环中则创建新线程运行以避免死锁。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result(timeout=30)
        else:
            return asyncio.run(coro)

    def get_llm_configs(self) -> List[Dict[str, Any]]:
        """
        获取所有已启用且可用的 LLM 模型配置列表。

        已弃用：请使用 unified_llm_service.get_available_models() 替代。
        此方法内部委托给 UnifiedLLMService，确保返回结果与新服务一致。

        Returns:
            List[Dict[str, Any]]: 模型配置字典列表

        Requirements: 4.2
        """
        warnings.warn(
            "ConfigProvider.get_llm_configs() 已弃用，请使用 unified_llm_service.get_available_models()",
            DeprecationWarning,
            stacklevel=2,
        )

        try:
            from app.services.unified_llm_service import unified_llm_service
            from dataclasses import asdict

            merged_models = self._run_async(
                unified_llm_service.get_available_models()
            )

            return [asdict(m) for m in merged_models]

        except Exception as e:
            logger.warning(
                "ConfigProvider.get_llm_configs() 委托 unified_llm_service 失败，回退到旧逻辑: %s", e
            )
            return self._get_llm_configs_legacy()

    def get_default_model(self) -> Optional[str]:
        """
        获取系统默认 LLM 模型名称。

        已弃用：请使用 unified_llm_service.get_default_model() 替代。
        此方法内部委托给 UnifiedLLMService。

        Returns:
            默认模型名称字符串，不可用时返回 None

        Requirements: 4.2
        """
        warnings.warn(
            "ConfigProvider.get_default_model() 已弃用，请使用 unified_llm_service.get_default_model()",
            DeprecationWarning,
            stacklevel=2,
        )

        try:
            from app.services.unified_llm_service import unified_llm_service

            merged = self._run_async(
                unified_llm_service.get_default_model()
            )

            if merged is not None:
                return merged.model_name

            # unified_llm_service 返回 None，回退到旧逻辑
            return self._get_default_model_legacy()

        except Exception as e:
            logger.warning(
                "ConfigProvider.get_default_model() 委托 unified_llm_service 失败，回退到旧逻辑: %s", e
            )
            return self._get_default_model_legacy()

    def get_model_config(self, model_name: str) -> Optional[Dict[str, Any]]:
        """
        获取指定模型的完整配置。

        已弃用：请使用 unified_llm_service.get_model_config() 替代。
        此方法内部委托给 UnifiedLLMService。

        Args:
            model_name: 模型名称标识

        Returns:
            模型配置字典，不存在时返回 None

        Requirements: 4.2
        """
        warnings.warn(
            "ConfigProvider.get_model_config() 已弃用，请使用 unified_llm_service.get_model_config()",
            DeprecationWarning,
            stacklevel=2,
        )

        try:
            from app.services.unified_llm_service import unified_llm_service
            from dataclasses import asdict

            merged = self._run_async(
                unified_llm_service.get_model_config(model_name)
            )

            if merged is not None:
                return asdict(merged)
            return None

        except Exception as e:
            logger.warning(
                "ConfigProvider.get_model_config(%s) 委托 unified_llm_service 失败: %s",
                model_name,
                e,
            )
            return None

    def _get_llm_configs_legacy(self) -> List[Dict[str, Any]]:
        """
        旧的 LLM 配置读取逻辑（从系统配置中读取）。

        作为 unified_llm_service 不可用时的回退方案。
        """
        try:
            cfg = self._run_async(config_service.get_system_config())
            if cfg and hasattr(cfg, "llm_configs") and cfg.llm_configs:
                result = []
                for llm_cfg in cfg.llm_configs:
                    try:
                        if hasattr(llm_cfg, "model_dump"):
                            result.append(llm_cfg.model_dump())
                        elif hasattr(llm_cfg, "dict"):
                            result.append(llm_cfg.dict())
                        elif isinstance(llm_cfg, dict):
                            result.append(llm_cfg)
                    except Exception:
                        continue
                return result
        except Exception as e:
            logger.warning("旧逻辑读取 LLM 配置失败: %s", e)
        return []

    def _get_default_model_legacy(self) -> Optional[str]:
        """
        旧的默认模型读取逻辑。

        作为 unified_llm_service 不可用时的回退方案。
        按优先级回退：default_llm -> quick_analysis_model -> qwen-turbo
        """
        try:
            cfg = self._run_async(config_service.get_system_config())
            if cfg:
                # 优先使用 default_llm
                if hasattr(cfg, "default_llm") and cfg.default_llm:
                    return cfg.default_llm
                # 回退到 system_settings 中的 quick_analysis_model
                if hasattr(cfg, "system_settings") and cfg.system_settings:
                    settings = cfg.system_settings
                    if isinstance(settings, dict):
                        return settings.get("quick_analysis_model", "qwen-turbo")
        except Exception as e:
            logger.warning("旧逻辑读取默认模型失败: %s", e)
        return "qwen-turbo"


# Module-level singleton
provider = ConfigProvider(ttl_seconds=60)

