"""
统一 LLM 配置服务

所有 LLM 配置的唯一入口，以 llm_providers 集合为 API Key 的 Single Source of Truth。
通过 Redis 缓存 + 缓存失效机制确保配置变更实时生效，并向后兼容现有接口。

Requirements: 2.1, 2.2, 2.3, 2.4, 4.3, 4.4
"""

import json
import logging
from datetime import datetime
from typing import List, Optional, Callable

from bson import ObjectId
from cachetools import TTLCache

from app.core.database import get_mongo_db
from app.models.config import (
    LLMConfig,
    LLMProvider,
    MergedModelConfig,
    ConfigChangedEvent,
)
from app.utils.timezone import now_tz

logger = logging.getLogger(__name__)


class UnifiedLLMService:
    """
    统一 LLM 配置服务 - 所有 LLM 配置的唯一入口

    以 llm_providers MongoDB 集合为 API Key 的 Single Source of Truth，
    合并 provider 厂家信息与 model 模型参数，提供统一的配置查询、写入、缓存管理和事件机制。
    """

    def __init__(self, redis_client=None, cache_ttl: int = 60):
        """
        初始化统一 LLM 配置服务。

        Args:
            redis_client: Redis 异步客户端实例，None 时回退到内存缓存
            cache_ttl: 缓存 TTL（秒），默认 60
        """
        self._redis = redis_client
        self._cache_ttl = cache_ttl

        # 内存缓存：Redis 不可用时的回退方案
        # maxsize=256 足以覆盖所有 provider + model + 列表缓存
        self._memory_cache: TTLCache = TTLCache(maxsize=256, ttl=cache_ttl)

        # 事件监听器列表：配置变更时通知已注册的回调
        self._listeners: List[Callable] = []

        logger.info(
            "UnifiedLLMService 初始化完成 (redis=%s, cache_ttl=%ds)",
            "已连接" if redis_client else "未连接（使用内存缓存）",
            cache_ttl,
        )

    # === 缓存读写层 ===

    async def _get_from_cache(self, key: str) -> Optional[dict]:
        """
        从缓存读取数据，优先 Redis，异常时回退到内存缓存。

        Args:
            key: 缓存 key，如 llm:provider:{name}、llm:model:{model_name}

        Returns:
            缓存的字典数据，未命中时返回 None
        """
        try:
            if self._redis:
                data = await self._redis.get(key)
                if data:
                    return json.loads(data)
        except Exception as e:
            logger.warning("Redis 读取失败，回退到内存缓存: %s", e)
            return self._memory_cache.get(key)
        return None

    async def _set_cache(self, key: str, data: dict, ttl: Optional[int] = None):
        """
        写入缓存，优先写 Redis，异常时写内存缓存。

        Args:
            key: 缓存 key
            data: 要缓存的字典数据
            ttl: 缓存 TTL（秒），默认使用 self._cache_ttl
        """
        if ttl is None:
            ttl = self._cache_ttl
        try:
            if self._redis:
                await self._redis.setex(key, ttl, json.dumps(data, ensure_ascii=False))
                return
        except Exception as e:
            logger.warning("Redis 写入失败，回退到内存缓存: %s", e)
        # Redis 不可用或未配置时，写入内存缓存
        self._memory_cache[key] = data

    # === 核心查询接口 ===

    @staticmethod
    def _is_valid_api_key(api_key: Optional[str]) -> bool:
        """
        判断 API Key 是否有效（非空、非 None、非占位符字符串）。

        占位符字符串包括：
        - "your-api-key-here"、"sk-xxx"、"请填写"、"your_api_key" 等常见占位符
        - 空字符串或纯空白字符串

        Args:
            api_key: 待验证的 API Key

        Returns:
            True 表示有效，False 表示无效
        """
        if not api_key or not api_key.strip():
            return False

        # 常见占位符列表（全部小写比较）
        placeholders = {
            "your-api-key-here",
            "your_api_key_here",
            "your-api-key",
            "your_api_key",
            "sk-xxx",
            "sk-xxxx",
            "sk-xxxxxxxx",
            "请填写",
            "请输入",
            "填写你的api key",
            "填写你的apikey",
            "your api key",
            "api-key-here",
            "api_key_here",
            "replace-with-your-key",
            "replace_with_your_key",
            "none",
            "null",
            "undefined",
            "todo",
            "xxx",
            "test",
            "demo",
        }

        key_lower = api_key.strip().lower()
        return key_lower not in placeholders

    async def get_available_models(self) -> List[MergedModelConfig]:
        """
        获取所有已启用且 API Key 有效的 LLM 模型列表。

        返回的模型需同时满足：
        - LLMConfig.enabled == True
        - 对应 LLMProvider.is_active == True
        - 对应 LLMProvider.api_key 有效（非空非占位符）

        Returns:
            已启用且可用的 LLM 模型配置列表（合并后的完整配置）
        """
        # 1. 尝试从缓存读取
        cache_key = "llm:available"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            try:
                return [MergedModelConfig(**item) for item in cached]
            except Exception as e:
                logger.warning("可用模型缓存数据反序列化失败: %s", e)

        # 2. 缓存未命中，从 MongoDB 查询 system_configs.llm_configs
        try:
            db = get_mongo_db()
            config_collection = db.system_configs

            # 查询当前激活的系统配置
            config_doc = await config_collection.find_one(
                {"is_active": True},
                sort=[("version", -1)],
            )

            if not config_doc:
                logger.warning("未找到激活的系统配置，无法获取可用模型列表")
                return []

            llm_configs = config_doc.get("llm_configs", [])
            available_models: List[MergedModelConfig] = []

            for cfg in llm_configs:
                try:
                    model_config = LLMConfig(**cfg)

                    # 过滤条件 1：模型必须启用
                    if not model_config.enabled:
                        continue

                    # 获取对应厂家信息
                    provider_name = model_config.provider
                    # 兼容历史枚举对象
                    if hasattr(provider_name, "value"):
                        provider_name = provider_name.value
                    provider_name = str(provider_name).strip()

                    provider = await self.get_provider_config(provider_name)
                    if provider is None:
                        continue

                    # 过滤条件 2：厂家必须启用
                    if not provider.is_active:
                        continue

                    # 过滤条件 3：API Key 必须有效
                    if not self._is_valid_api_key(provider.api_key):
                        continue

                    # 合并配置
                    merged = self._merge_config(provider, model_config)
                    available_models.append(merged)

                except Exception as e:
                    logger.warning("处理模型配置时出错，跳过: %s", e)

            # 3. 写入缓存
            from dataclasses import asdict

            cache_data = [asdict(m) for m in available_models]
            await self._set_cache(cache_key, cache_data)

            logger.info("获取到 %d 个可用模型配置", len(available_models))
            return available_models

        except RuntimeError:
            logger.error("MongoDB 未初始化，无法查询可用模型列表")
            return []
        except Exception as e:
            logger.error("查询可用模型列表失败: %s", e)
            return []

    async def get_model_config(self, model_name: str) -> Optional[MergedModelConfig]:
        """
        获取指定模型的完整配置（合并 provider + model 参数）。

        从 system_configs.llm_configs 查找模型配置，再从 llm_providers 获取
        对应厂家信息，合并后返回完整配置。api_key 始终来自 LLMProvider。

        Args:
            model_name: 模型名称标识

        Returns:
            合并后的模型完整配置，模型不存在或未启用时返回 None
        """
        # 1. 尝试从缓存读取
        cache_key = f"llm:model:{model_name}"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            try:
                return MergedModelConfig(**cached)
            except Exception as e:
                logger.warning("模型缓存数据反序列化失败 (key=%s): %s", cache_key, e)

        # 2. 缓存未命中，从 MongoDB 查询 system_configs.llm_configs
        try:
            db = get_mongo_db()
            config_collection = db.system_configs

            # 查询当前激活的系统配置
            config_doc = await config_collection.find_one(
                {"is_active": True},
                sort=[("version", -1)],
            )

            if not config_doc:
                logger.warning("未找到激活的系统配置，无法获取模型: %s", model_name)
                return None

            # 从 llm_configs 数组中查找匹配的模型
            llm_configs = config_doc.get("llm_configs", [])
            model_doc = None
            for cfg in llm_configs:
                if cfg.get("model_name") == model_name:
                    model_doc = cfg
                    break

            if model_doc is None:
                logger.warning("模型 %s 不存在于 system_configs.llm_configs 中", model_name)
                return None

            # 反序列化为 LLMConfig
            model_config = LLMConfig(**model_doc)

            # 检查模型是否启用
            if not model_config.enabled:
                logger.warning("模型 %s 已禁用", model_name)
                return None

            # 3. 获取对应厂家信息
            provider_name = model_config.provider
            # 兼容历史枚举对象
            if hasattr(provider_name, "value"):
                provider_name = provider_name.value
            provider_name = str(provider_name).strip()

            provider = await self.get_provider_config(provider_name)
            if provider is None:
                logger.warning(
                    "模型 %s 对应的厂家 %s 不存在，无法合并配置",
                    model_name,
                    provider_name,
                )
                return None

            # 检查厂家是否启用
            if not provider.is_active:
                logger.warning(
                    "模型 %s 对应的厂家 %s 已禁用",
                    model_name,
                    provider_name,
                )
                return None

            # 4. 合并配置
            merged = self._merge_config(provider, model_config)

            # 5. 写入缓存
            from dataclasses import asdict

            await self._set_cache(cache_key, asdict(merged))

            return merged

        except RuntimeError:
            logger.error("MongoDB 未初始化，无法查询模型配置: %s", model_name)
            return None
        except Exception as e:
            logger.error("查询模型配置失败 (model=%s): %s", model_name, e)
            return None

    async def get_default_model(self) -> Optional[MergedModelConfig]:
        """
        获取系统默认 LLM 模型的完整配置。

        从 system_configs.default_llm 获取默认模型名，
        再调用 get_model_config 返回完整配置。

        Returns:
            默认模型的完整配置，未设置或不可用时返回 None
        """
        # 1. 尝试从缓存读取
        cache_key = "llm:default"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            try:
                return MergedModelConfig(**cached)
            except Exception as e:
                logger.warning("默认模型缓存数据反序列化失败: %s", e)

        # 2. 缓存未命中，从 MongoDB 查询 system_configs.default_llm
        try:
            db = get_mongo_db()
            config_collection = db.system_configs

            # 查询当前激活的系统配置
            config_doc = await config_collection.find_one(
                {"is_active": True},
                sort=[("version", -1)],
            )

            if not config_doc:
                logger.warning("未找到激活的系统配置，无法获取默认模型")
                return None

            default_model_name = config_doc.get("default_llm")
            if not default_model_name:
                logger.warning("系统配置中未设置 default_llm")
                return None

            # 3. 调用 get_model_config 获取完整配置
            merged = await self.get_model_config(default_model_name)
            if merged is None:
                logger.warning("默认模型 %s 不可用", default_model_name)
                return None

            # 4. 写入缓存
            from dataclasses import asdict

            await self._set_cache(cache_key, asdict(merged))

            return merged

        except RuntimeError:
            logger.error("MongoDB 未初始化，无法查询默认模型")
            return None
        except Exception as e:
            logger.error("查询默认模型失败: %s", e)
            return None

    async def recommend_model(
        self, task_type: str = "analysis", depth: str = "标准"
    ) -> Optional[MergedModelConfig]:
        """
        根据任务类型和分析深度推荐合适的模型。

        深度映射：快速→1, 基础→2, 标准→3, 深度→4, 专业→5
        从可用模型中筛选满足能力要求的模型，按 capability_level 升序排序，
        选择刚好满足要求的最低级别模型，避免浪费高级模型资源。

        Args:
            task_type: 任务类型，如 "analysis"、"screening" 等
            depth: 分析深度，如 "快速"、"基础"、"标准"、"深度"、"专业"

        Returns:
            推荐的模型配置，无合适模型时返回 None
        """
        # 深度到最低 capability_level 的映射
        depth_to_min_level = {
            "快速": 1,
            "基础": 2,
            "标准": 3,
            "深度": 4,
            "专业": 5,
        }

        min_level = depth_to_min_level.get(depth, 3)

        # 获取所有可用模型
        available = await self.get_available_models()
        if not available:
            logger.warning("无可用模型，无法推荐 (task_type=%s, depth=%s)", task_type, depth)
            return None

        # 过滤满足最低能力要求的模型
        candidates = [m for m in available if m.capability_level >= min_level]

        if not candidates:
            logger.warning(
                "没有满足能力要求的模型 (task_type=%s, depth=%s, min_level=%d)",
                task_type,
                depth,
                min_level,
            )
            return None

        # 按 capability_level 升序排序（选择刚好满足要求的最低级别模型，避免浪费）
        candidates.sort(key=lambda m: m.capability_level)

        recommended = candidates[0]
        logger.info(
            "推荐模型: %s (capability_level=%d, task_type=%s, depth=%s)",
            recommended.model_name,
            recommended.capability_level,
            task_type,
            depth,
        )
        return recommended

    @staticmethod
    def _merge_config(provider: LLMProvider, model: LLMConfig) -> MergedModelConfig:
        """
        合并厂家配置和模型配置为运行时使用的 MergedModelConfig。

        合并规则：
        - api_key: 始终来自 LLMProvider
        - api_base: 优先取 LLMConfig.api_base（非空时），回退 LLMProvider.default_base_url
        - enabled: LLMConfig.enabled AND LLMProvider.is_active 都为 True
        - 其余模型参数来自 LLMConfig

        Args:
            provider: 厂家配置对象
            model: 模型配置对象

        Returns:
            合并后的运行时配置
        """
        # api_base 优先取模型自定义值，回退厂家默认值
        api_base = model.api_base if model.api_base else (provider.default_base_url or "")

        return MergedModelConfig(
            # 来自 LLMConfig 的模型参数
            model_name=model.model_name,
            model_display_name=model.model_display_name,
            max_tokens=model.max_tokens,
            temperature=model.temperature,
            timeout=model.timeout,
            retry_times=model.retry_times,
            enabled=model.enabled and provider.is_active,
            capability_level=model.capability_level,
            suitable_roles=list(model.suitable_roles),
            features=list(model.features),
            # 来自 LLMProvider 的厂家信息
            provider_name=provider.name,
            api_key=provider.api_key or "",
            api_base=api_base,
            provider_display_name=provider.display_name,
            is_aggregator=provider.is_aggregator,
            # 定价信息（来自 LLMConfig）
            input_price_per_1k=model.input_price_per_1k,
            output_price_per_1k=model.output_price_per_1k,
            currency=model.currency,
        )

    async def get_provider_config(self, provider_name: str) -> Optional[LLMProvider]:
        """
        获取指定厂家的完整配置。

        从缓存或 llm_providers 集合查询单个厂家配置。

        Args:
            provider_name: 厂家唯一标识

        Returns:
            厂家配置对象，不存在时返回 None
        """
        # 1. 尝试从缓存读取
        cache_key = f"llm:provider:{provider_name}"
        cached = await self._get_from_cache(cache_key)
        if cached is not None:
            try:
                return LLMProvider(**cached)
            except Exception as e:
                logger.warning("缓存数据反序列化失败 (key=%s): %s", cache_key, e)

        # 2. 缓存未命中，从 MongoDB 查询
        try:
            db = get_mongo_db()
            providers_collection = db.llm_providers
            provider_doc = await providers_collection.find_one({"name": provider_name})

            if not provider_doc:
                logger.warning("厂家 %s 不存在", provider_name)
                return None

            provider = LLMProvider(**provider_doc)

            # 3. 写入缓存（使用 mode="json" 确保 datetime/ObjectId 可序列化）
            await self._set_cache(cache_key, provider.model_dump(mode="json"))

            return provider

        except RuntimeError:
            # get_mongo_db() 在数据库未初始化时抛出 RuntimeError
            logger.error("MongoDB 未初始化，无法查询厂家配置: %s", provider_name)
            return None
        except Exception as e:
            logger.error("查询厂家配置失败 (provider=%s): %s", provider_name, e)
            return None

    async def get_all_providers(self) -> List[LLMProvider]:
        """
        获取所有启用的厂家列表。

        查询 llm_providers 集合中所有 is_active == True 的厂家。

        Returns:
            所有启用的厂家配置列表
        """
        try:
            db = get_mongo_db()
            providers_collection = db.llm_providers

            providers_data = await providers_collection.find(
                {"is_active": True}
            ).to_list(length=None)

            providers: List[LLMProvider] = []
            for doc in providers_data:
                try:
                    provider = LLMProvider(**doc)
                    providers.append(provider)

                    # 将每个厂家写入缓存，方便后续 get_provider_config 命中
                    cache_key = f"llm:provider:{provider.name}"
                    await self._set_cache(cache_key, provider.model_dump(mode="json"))
                except Exception as e:
                    logger.warning("厂家文档反序列化失败，跳过: %s", e)

            logger.info("获取到 %d 个启用的厂家配置", len(providers))
            return providers

        except RuntimeError:
            logger.error("MongoDB 未初始化，无法查询厂家列表")
            return []
        except Exception as e:
            logger.error("查询所有启用厂家失败: %s", e)
            return []

    # === 写入接口（触发同步 + 缓存失效） ===

    async def _find_provider_by_id(self, provider_id: str):
        """
        通过 ID 查找厂家文档，兼容 ObjectId 和字符串两种 _id 类型。

        Args:
            provider_id: 厂家文档 ID（可能是 ObjectId 字符串或普通字符串）

        Returns:
            (providers_collection, provider_doc) 元组，未找到时 provider_doc 为 None
        """
        db = get_mongo_db()
        providers_collection = db.llm_providers

        provider_doc = None
        try:
            # 先尝试作为 ObjectId 查询
            provider_doc = await providers_collection.find_one(
                {"_id": ObjectId(provider_id)}
            )
        except Exception:
            pass

        # 如果 ObjectId 查询未命中，再尝试字符串查询
        if provider_doc is None:
            provider_doc = await providers_collection.find_one(
                {"_id": provider_id}
            )

        return providers_collection, provider_doc

    async def update_provider(self, provider_id: str, update_data: dict) -> dict:
        """
        更新厂家配置并级联同步。

        更新 llm_providers 集合中的厂家文档，并根据变更内容级联同步
        system_configs.llm_configs 中的相关条目：
        - 若更新了 api_key：同步更新所有引用该厂家的 llm_configs 条目的 api_key
        - 若更新了 default_base_url：同步更新引用该厂家且未自定义 api_base 的条目

        同步失败时记录 ERROR 日志，返回 sync_result 包含错误详情，原始更新仍成功。
        操作完成后调用 invalidate_cache 清除相关缓存。

        Args:
            provider_id: 厂家文档 ID
            update_data: 要更新的字段字典

        Returns:
            {"success": True/False, "sync_result": {...}}

        Requirements: 3.1, 3.2, 3.4
        """
        sync_result = {
            "api_key_synced": 0,
            "base_url_synced": 0,
            "errors": [],
        }

        try:
            # 1. 查找厂家文档
            providers_collection, provider_doc = await self._find_provider_by_id(
                provider_id
            )

            if provider_doc is None:
                logger.warning("更新厂家失败: 厂家 %s 不存在", provider_id)
                return {"success": False, "sync_result": sync_result}

            provider_name = provider_doc.get("name", "")

            # 2. 更新 llm_providers 集合中的厂家文档
            update_data["updated_at"] = now_tz()
            await providers_collection.update_one(
                {"_id": provider_doc["_id"]},
                {"$set": update_data},
            )
            logger.info("厂家 %s (%s) 更新成功", provider_name, provider_id)

            # 3. 级联同步 system_configs.llm_configs
            changed_fields = list(update_data.keys())

            # 3a. api_key 级联同步
            if "api_key" in update_data:
                try:
                    new_api_key = update_data["api_key"]
                    synced = await self._sync_api_key_to_configs(
                        provider_name, new_api_key
                    )
                    sync_result["api_key_synced"] = synced
                    logger.info(
                        "api_key 级联同步完成: provider=%s, 同步 %d 个条目",
                        provider_name,
                        synced,
                    )
                except Exception as e:
                    error_msg = f"api_key 级联同步失败: {e}"
                    logger.error(error_msg)
                    sync_result["errors"].append(error_msg)

            # 3b. default_base_url 条件同步
            if "default_base_url" in update_data:
                try:
                    new_base_url = update_data["default_base_url"]
                    synced = await self._sync_base_url_to_configs(
                        provider_name, new_base_url
                    )
                    sync_result["base_url_synced"] = synced
                    logger.info(
                        "default_base_url 条件同步完成: provider=%s, 同步 %d 个条目",
                        provider_name,
                        synced,
                    )
                except Exception as e:
                    error_msg = f"default_base_url 级联同步失败: {e}"
                    logger.error(error_msg)
                    sync_result["errors"].append(error_msg)

            # 4. 清除缓存
            await self.invalidate_cache()

            # 5. 发布配置变更事件
            event = ConfigChangedEvent(
                event_type="provider_updated",
                provider_name=provider_name,
                changed_fields=changed_fields,
                sync_result=sync_result,
            )
            self._emit_config_changed(event)

            return {"success": True, "sync_result": sync_result}

        except RuntimeError:
            logger.error("MongoDB 未初始化，无法更新厂家: %s", provider_id)
            return {"success": False, "sync_result": sync_result}
        except Exception as e:
            logger.error("更新厂家失败 (provider_id=%s): %s", provider_id, e)
            sync_result["errors"].append(str(e))
            return {"success": False, "sync_result": sync_result}

    async def _sync_api_key_to_configs(
        self, provider_name: str, new_api_key: str
    ) -> int:
        """
        将新的 api_key 同步到 system_configs.llm_configs 中所有引用该厂家的条目。

        Args:
            provider_name: 厂家唯一标识
            new_api_key: 新的 API Key 值

        Returns:
            同步更新的条目数量

        Requirements: 3.1
        """
        db = get_mongo_db()
        config_collection = db.system_configs

        # 查询当前激活的系统配置
        config_doc = await config_collection.find_one(
            {"is_active": True},
            sort=[("version", -1)],
        )

        if not config_doc:
            logger.warning("未找到激活的系统配置，跳过 api_key 级联同步")
            return 0

        llm_configs = config_doc.get("llm_configs", [])
        synced_count = 0

        for i, cfg in enumerate(llm_configs):
            cfg_provider = cfg.get("provider", "")
            # 兼容历史枚举值
            if hasattr(cfg_provider, "value"):
                cfg_provider = cfg_provider.value
            cfg_provider = str(cfg_provider).strip()

            if cfg_provider == provider_name:
                llm_configs[i]["api_key"] = new_api_key
                synced_count += 1

        if synced_count > 0:
            await config_collection.update_one(
                {"_id": config_doc["_id"]},
                {
                    "$set": {
                        "llm_configs": llm_configs,
                        "updated_at": now_tz(),
                    }
                },
            )

        return synced_count

    async def _sync_base_url_to_configs(
        self, provider_name: str, new_base_url: str
    ) -> int:
        """
        将新的 default_base_url 同步到 system_configs.llm_configs 中
        所有引用该厂家且未自定义 api_base 的条目。

        仅更新 api_base 为空或 None 的条目，已有自定义 api_base 的条目不受影响。

        Args:
            provider_name: 厂家唯一标识
            new_base_url: 新的默认 Base URL

        Returns:
            同步更新的条目数量

        Requirements: 3.2
        """
        db = get_mongo_db()
        config_collection = db.system_configs

        # 查询当前激活的系统配置
        config_doc = await config_collection.find_one(
            {"is_active": True},
            sort=[("version", -1)],
        )

        if not config_doc:
            logger.warning("未找到激活的系统配置，跳过 base_url 级联同步")
            return 0

        llm_configs = config_doc.get("llm_configs", [])
        synced_count = 0

        for i, cfg in enumerate(llm_configs):
            cfg_provider = cfg.get("provider", "")
            # 兼容历史枚举值
            if hasattr(cfg_provider, "value"):
                cfg_provider = cfg_provider.value
            cfg_provider = str(cfg_provider).strip()

            if cfg_provider == provider_name:
                # 仅更新 api_base 为空/None 的条目
                existing_api_base = cfg.get("api_base")
                if not existing_api_base:
                    llm_configs[i]["api_base"] = new_base_url
                    synced_count += 1

        if synced_count > 0:
            await config_collection.update_one(
                {"_id": config_doc["_id"]},
                {
                    "$set": {
                        "llm_configs": llm_configs,
                        "updated_at": now_tz(),
                    }
                },
            )

        return synced_count

    async def disable_provider(self, provider_id: str) -> dict:
        """
        禁用厂家并级联禁用所有引用该厂家的模型。

        将 llm_providers 中该厂家的 is_active 设为 False，
        并级联将 system_configs.llm_configs 中所有引用该厂家的条目 enabled 设为 False。
        操作完成后清除缓存并发布 ConfigChangedEvent 事件。

        Args:
            provider_id: 厂家文档 ID

        Returns:
            {"success": True/False, "sync_result": {...}}

        Requirements: 3.3
        """
        sync_result = {
            "models_disabled": 0,
            "errors": [],
        }

        try:
            # 1. 查找厂家文档（复用 _find_provider_by_id 辅助方法）
            providers_collection, provider_doc = await self._find_provider_by_id(
                provider_id
            )

            if provider_doc is None:
                logger.warning("禁用厂家失败: 厂家 %s 不存在", provider_id)
                return {"success": False, "sync_result": sync_result}

            provider_name = provider_doc.get("name", "")

            # 2. 将 llm_providers 中该厂家的 is_active 设为 False
            await providers_collection.update_one(
                {"_id": provider_doc["_id"]},
                {"$set": {"is_active": False, "updated_at": now_tz()}},
            )
            logger.info("厂家 %s (%s) 已禁用", provider_name, provider_id)

            # 3. 级联禁用 system_configs.llm_configs 中所有引用该厂家的条目
            try:
                disabled_count = await self._cascade_disable_models(provider_name)
                sync_result["models_disabled"] = disabled_count
                logger.info(
                    "级联禁用完成: provider=%s, 禁用 %d 个模型条目",
                    provider_name,
                    disabled_count,
                )
            except Exception as e:
                error_msg = f"级联禁用模型失败: {e}"
                logger.error(error_msg)
                sync_result["errors"].append(error_msg)

            # 4. 清除缓存
            await self.invalidate_cache()

            # 5. 发布配置变更事件
            event = ConfigChangedEvent(
                event_type="provider_disabled",
                provider_name=provider_name,
                changed_fields=["is_active"],
                sync_result=sync_result,
            )
            self._emit_config_changed(event)

            return {"success": True, "sync_result": sync_result}

        except RuntimeError:
            logger.error("MongoDB 未初始化，无法禁用厂家: %s", provider_id)
            return {"success": False, "sync_result": sync_result}
        except Exception as e:
            logger.error("禁用厂家失败 (provider_id=%s): %s", provider_id, e)
            sync_result["errors"].append(str(e))
            return {"success": False, "sync_result": sync_result}

    async def _cascade_disable_models(self, provider_name: str) -> int:
        """
        级联禁用 system_configs.llm_configs 中所有引用该厂家的条目。

        将所有 provider == provider_name 的 llm_configs 条目的 enabled 设为 False。

        Args:
            provider_name: 厂家唯一标识

        Returns:
            禁用的条目数量

        Requirements: 3.3
        """
        db = get_mongo_db()
        config_collection = db.system_configs

        # 查询当前激活的系统配置
        config_doc = await config_collection.find_one(
            {"is_active": True},
            sort=[("version", -1)],
        )

        if not config_doc:
            logger.warning("未找到激活的系统配置，跳过级联禁用")
            return 0

        llm_configs = config_doc.get("llm_configs", [])
        disabled_count = 0

        for i, cfg in enumerate(llm_configs):
            cfg_provider = cfg.get("provider", "")
            # 兼容历史枚举值
            if hasattr(cfg_provider, "value"):
                cfg_provider = cfg_provider.value
            cfg_provider = str(cfg_provider).strip()

            if cfg_provider == provider_name:
                llm_configs[i]["enabled"] = False
                disabled_count += 1

        if disabled_count > 0:
            await config_collection.update_one(
                {"_id": config_doc["_id"]},
                {
                    "$set": {
                        "llm_configs": llm_configs,
                        "updated_at": now_tz(),
                    }
                },
            )

        return disabled_count

    # === 缓存管理 ===

    async def invalidate_cache(self, pattern: str = "*"):
        """
        主动失效缓存。

        删除匹配 pattern 的 Redis 缓存 key，同时清空内存缓存。
        缓存失效失败时设置 TTL=0 强制下次请求重新加载，记录 ERROR 日志。

        Args:
            pattern: 缓存 key 匹配模式，默认 "*" 清除所有 LLM 相关缓存

        Requirements: 5.1, 5.2, 5.5
        """
        # 清空内存缓存（无论 Redis 操作是否成功）
        self._memory_cache.clear()

        if not self._redis:
            logger.info("Redis 未连接，仅清空内存缓存")
            return

        try:
            # 构建完整的匹配模式：在 llm: 命名空间下匹配
            full_pattern = f"llm:{pattern}" if not pattern.startswith("llm:") else pattern
            # 使用 SCAN 遍历匹配的 key 并删除
            deleted_count = 0
            async for key in self._redis.scan_iter(match=full_pattern):
                await self._redis.delete(key)
                deleted_count += 1
            logger.info("缓存失效完成: pattern=%s, 删除 %d 个 key", full_pattern, deleted_count)
        except Exception as e:
            logger.error("缓存失效操作失败 (pattern=%s): %s", pattern, e)
            # 回退策略：尝试设置 TTL=0 强制下次请求重新加载
            await self._force_expire_known_keys()

    async def _force_expire_known_keys(self):
        """
        回退策略：当缓存失效操作（SCAN + DELETE）失败时，
        尝试对所有已知的静态缓存 key 设置 TTL=0，强制 Redis 在下次请求时过期。
        对于动态 key（provider:*、model:*），尝试用 SCAN 查找并逐个过期。

        Requirements: 5.5
        """
        # 已知的静态缓存 key
        static_keys = [
            "llm:available",
            "llm:default",
        ]
        expired_count = 0

        # 1. 先处理静态 key
        for key in static_keys:
            try:
                await self._redis.expire(key, 0)
                expired_count += 1
            except Exception:
                pass

        # 2. 尝试 SCAN 查找动态 key（provider:*、model:*）并设置 TTL=0
        try:
            async for key in self._redis.scan_iter(match="llm:*"):
                try:
                    await self._redis.expire(key, 0)
                    expired_count += 1
                except Exception:
                    pass
        except Exception:
            pass

        if expired_count > 0:
            logger.warning(
                "缓存失效回退: 已对 %d 个 key 设置 TTL=0 强制过期", expired_count
            )
        else:
            logger.error("设置 TTL=0 也失败，缓存可能暂时不一致")

    # === 事件机制 ===

    def on_config_changed(self, callback: Callable):
        """
        注册配置变更监听器。

        当 LLM 厂家配置或模型配置发生变更时，已注册的回调函数将被调用。

        Args:
            callback: 回调函数，接收 ConfigChangedEvent 参数
        """
        self._listeners.append(callback)
        logger.info("已注册配置变更监听器: %s", callback.__name__ if hasattr(callback, '__name__') else str(callback))

    def _emit_config_changed(self, event: ConfigChangedEvent):
        """
        发布配置变更事件。

        遍历所有已注册的监听器并调用，捕获异常避免影响主流程。

        Args:
            event: 配置变更事件对象
        """
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                listener_name = listener.__name__ if hasattr(listener, '__name__') else str(listener)
                logger.error("配置变更监听器 %s 执行失败: %s", listener_name, e)

    # === 迁移工具 ===

    async def migrate_from_legacy(self) -> dict:
        """
        从旧配置体系迁移数据到新体系。

        检测 llm_providers 集合是否为空，若为空则从 system_configs.llm_configs
        和 .env 环境变量（Env_Seed）中迁移数据。已有有效 api_key 的厂家不会被覆盖。

        迁移是幂等的：如果 llm_providers 已有数据则跳过从 system_configs 的迁移。
        Env_Seed 阶段始终执行，但仅在厂家无有效 api_key 时写入。

        迁移失败时记录详细错误日志并回退到旧配置读取路径。

        Returns:
            迁移结果摘要，包含迁移的厂家数量、Key 数量等信息

        Requirements: 1.3, 1.4, 7.4, 7.5
        """
        import os

        result = {
            "migrated_providers": 0,
            "env_seed_keys": 0,
            "skipped_existing_keys": 0,
            "errors": [],
            "fallback_to_legacy": False,
        }

        # 环境变量名称映射：provider name → 环境变量名
        env_key_mapping = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "dashscope": "DASHSCOPE_API_KEY",
            "qwen": "DASHSCOPE_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "gemini": "GOOGLE_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "302ai": "AI302_API_KEY",
            "ai302": "AI302_API_KEY",
            "aihubmix": "AIHUBMIX_API_KEY",
            "oneapi": "ONEAPI_API_KEY",
            "newapi": "ONEAPI_API_KEY",
            "qianfan": "QIANFAN_API_KEY",
            "custom_openai": "CUSTOM_OPENAI_API_KEY",
        }

        try:
            db = get_mongo_db()
            providers_collection = db.llm_providers
            config_collection = db.system_configs

            # === 阶段 1：检测 llm_providers 集合是否为空 ===
            existing_count = await providers_collection.count_documents({})
            logger.info(
                "迁移检测: llm_providers 集合中有 %d 条记录", existing_count
            )

            # === 阶段 2：若为空，从 system_configs.llm_configs 提取 provider 信息 ===
            if existing_count == 0:
                logger.info("llm_providers 集合为空，开始从 system_configs 迁移数据")

                config_doc = await config_collection.find_one(
                    {"is_active": True},
                    sort=[("version", -1)],
                )

                if config_doc:
                    llm_configs = config_doc.get("llm_configs", [])
                    # 按 provider 分组，提取唯一的厂家信息
                    providers_to_create: dict = {}

                    for cfg in llm_configs:
                        try:
                            provider_name = cfg.get("provider", "")
                            if hasattr(provider_name, "value"):
                                provider_name = provider_name.value
                            provider_name = str(provider_name).strip()

                            if not provider_name:
                                continue

                            # 每个 provider 只处理一次（取第一个遇到的配置）
                            if provider_name in providers_to_create:
                                continue

                            # 从 llm_config 条目中提取厂家级别信息
                            api_key = cfg.get("api_key")
                            api_base = cfg.get("api_base")
                            display_name = cfg.get(
                                "model_display_name", provider_name
                            )

                            providers_to_create[provider_name] = {
                                "name": provider_name,
                                "display_name": display_name,
                                "is_active": True,
                                "api_key": api_key if self._is_valid_api_key(api_key) else None,
                                "default_base_url": api_base,
                                "description": f"从 system_configs 迁移的 {provider_name} 厂家配置",
                                "supported_features": ["chat"],
                                "aliases": [],
                                "extra_config": {},
                                "is_aggregator": False,
                                "created_at": now_tz(),
                                "updated_at": now_tz(),
                            }
                        except Exception as e:
                            error_msg = f"处理 llm_config 条目时出错: {e}"
                            logger.warning(error_msg)
                            result["errors"].append(error_msg)

                    # 批量写入 llm_providers
                    if providers_to_create:
                        try:
                            docs_to_insert = list(providers_to_create.values())
                            await providers_collection.insert_many(docs_to_insert)
                            result["migrated_providers"] = len(docs_to_insert)
                            logger.info(
                                "成功从 system_configs 迁移 %d 个厂家到 llm_providers",
                                len(docs_to_insert),
                            )
                        except Exception as e:
                            error_msg = f"批量写入 llm_providers 失败: {e}"
                            logger.error(error_msg)
                            result["errors"].append(error_msg)
                    else:
                        logger.info("system_configs.llm_configs 中未找到可迁移的厂家信息")
                else:
                    logger.warning("未找到激活的系统配置，跳过 system_configs 迁移阶段")
            else:
                logger.info(
                    "llm_providers 已有 %d 条记录，跳过 system_configs 迁移阶段",
                    existing_count,
                )

            # === 阶段 3：从 .env 环境变量（Env_Seed）读取 API Key ===
            # 此阶段始终执行，但仅在厂家无有效 api_key 时写入（Requirements 1.4）
            logger.info("开始 Env_Seed 阶段：从环境变量补充 API Key")

            # 重新查询所有厂家（包括刚迁移的）
            all_providers = await providers_collection.find({}).to_list(length=None)

            for provider_doc in all_providers:
                try:
                    provider_name = provider_doc.get("name", "")
                    existing_api_key = provider_doc.get("api_key")

                    # 如果已有有效 api_key，跳过（Requirements 1.4）
                    if self._is_valid_api_key(existing_api_key):
                        result["skipped_existing_keys"] += 1
                        logger.debug(
                            "厂家 %s 已有有效 api_key，跳过 Env_Seed",
                            provider_name,
                        )
                        continue

                    # 查找对应的环境变量
                    env_var_name = env_key_mapping.get(provider_name)
                    if not env_var_name:
                        # 尝试通用格式：{PROVIDER_NAME}_API_KEY
                        env_var_name = f"{provider_name.upper()}_API_KEY"

                    env_value = os.environ.get(env_var_name)

                    if self._is_valid_api_key(env_value):
                        # 将环境变量中的 API Key 写入 llm_providers
                        await providers_collection.update_one(
                            {"_id": provider_doc["_id"]},
                            {
                                "$set": {
                                    "api_key": env_value,
                                    "updated_at": now_tz(),
                                }
                            },
                        )
                        result["env_seed_keys"] += 1
                        logger.info(
                            "Env_Seed: 从环境变量 %s 写入厂家 %s 的 API Key",
                            env_var_name,
                            provider_name,
                        )
                    else:
                        logger.debug(
                            "环境变量 %s 无有效值，跳过厂家 %s",
                            env_var_name,
                            provider_name,
                        )
                except Exception as e:
                    error_msg = f"Env_Seed 处理厂家 {provider_doc.get('name', '?')} 时出错: {e}"
                    logger.warning(error_msg)
                    result["errors"].append(error_msg)

            # 迁移完成后清除缓存，确保后续请求获取最新数据
            await self.invalidate_cache("*")

            logger.info(
                "数据迁移完成: 迁移厂家=%d, Env_Seed Key=%d, 跳过已有Key=%d, 错误=%d",
                result["migrated_providers"],
                result["env_seed_keys"],
                result["skipped_existing_keys"],
                len(result["errors"]),
            )

            return result

        except RuntimeError:
            # MongoDB 未初始化
            error_msg = "MongoDB 未初始化，迁移失败，回退到旧配置读取路径"
            logger.error(error_msg)
            result["errors"].append(error_msg)
            result["fallback_to_legacy"] = True
            return result
        except Exception as e:
            error_msg = f"数据迁移过程中发生异常: {e}"
            logger.error(error_msg, exc_info=True)
            result["errors"].append(error_msg)
            result["fallback_to_legacy"] = True
            return result


# 模块级单例
unified_llm_service = UnifiedLLMService()
