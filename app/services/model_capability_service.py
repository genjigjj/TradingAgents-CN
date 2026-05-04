"""
模型能力管理服务

提供模型能力评估、验证和推荐功能。
通过 unified_llm_service 获取 LLM 配置（Requirements: 4.6）。
"""

import asyncio
import concurrent.futures
import logging
import re
from typing import Tuple, Dict, Optional, List, Any

from app.constants.model_capabilities import (
    ANALYSIS_DEPTH_REQUIREMENTS,
    DEFAULT_MODEL_CAPABILITIES,
    CAPABILITY_DESCRIPTIONS,
    ModelRole,
    ModelFeature
)

logger = logging.getLogger(__name__)


def _run_async(coro):
    """
    在同步上下文中运行异步协程。

    如果当前线程没有事件循环，则创建新的事件循环运行协程。
    如果当前已在事件循环中（如 FastAPI 请求处理中），
    则在新线程中运行以避免死锁。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # 已在事件循环中，创建新线程运行协程以避免死锁
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        # 没有运行中的事件循环，直接创建新的
        return asyncio.run(coro)


class ModelCapabilityService:
    """模型能力管理服务"""

    def _parse_aggregator_model_name(self, model_name: str) -> Tuple[Optional[str], str]:
        """
        解析聚合渠道的模型名称

        Args:
            model_name: 模型名称，可能包含前缀（如 openai/gpt-4, anthropic/claude-3-sonnet）

        Returns:
            (原厂商, 原模型名) 元组
        """
        # 常见的聚合渠道模型名称格式：
        # - openai/gpt-4
        # - anthropic/claude-3-sonnet
        # - google/gemini-pro

        if "/" in model_name:
            parts = model_name.split("/", 1)
            if len(parts) == 2:
                provider_hint = parts[0].lower()
                original_model = parts[1]

                # 映射提供商提示到标准名称
                provider_map = {
                    "openai": "openai",
                    "anthropic": "anthropic",
                    "google": "google",
                    "deepseek": "deepseek",
                    "alibaba": "qwen",
                    "qwen": "qwen",
                    "zhipu": "glm",
                    "glm": "glm",
                    "baidu": "baidu",
                    "moonshot": "moonshot"
                }

                provider = provider_map.get(provider_hint)
                return provider, original_model

        return None, model_name

    def _get_model_capability_with_mapping(self, model_name: str) -> Tuple[int, Optional[str]]:
        """
        获取模型能力等级（支持聚合渠道映射）

        Returns:
            (能力等级, 映射的原模型名) 元组
        """
        # 1. 先尝试直接匹配
        if model_name in DEFAULT_MODEL_CAPABILITIES:
            return DEFAULT_MODEL_CAPABILITIES[model_name]["capability_level"], None

        # 2. 尝试解析聚合渠道模型名
        provider, original_model = self._parse_aggregator_model_name(model_name)

        if original_model and original_model != model_name:
            # 尝试用原模型名查找
            if original_model in DEFAULT_MODEL_CAPABILITIES:
                logger.info(f"🔄 聚合渠道模型映射: {model_name} -> {original_model}")
                return DEFAULT_MODEL_CAPABILITIES[original_model]["capability_level"], original_model

        # 3. 返回默认值
        return 2, None

    def get_model_capability(self, model_name: str) -> int:
        """
        获取模型的能力等级（支持聚合渠道模型映射）

        Args:
            model_name: 模型名称（可能包含聚合渠道前缀，如 openai/gpt-4）

        Returns:
            能力等级 (1-5)
        """
        # 1. 优先从 unified_llm_service 读取（Requirements: 4.6）
        try:
            from app.services.unified_llm_service import unified_llm_service
            available_models = _run_async(unified_llm_service.get_available_models())
            for model in available_models:
                if model.model_name == model_name:
                    return model.capability_level
        except Exception as e:
            logger.warning(f"从 unified_llm_service 读取模型能力失败: {e}")

        # 2. 从默认映射表读取（支持聚合渠道映射）
        capability, mapped_model = self._get_model_capability_with_mapping(model_name)
        if mapped_model:
            logger.info(f"✅ 使用映射模型 {mapped_model} 的能力等级: {capability}")

        return capability
    
    def get_model_config(self, model_name: str) -> Dict[str, Any]:
        """
        获取模型的完整配置信息（支持聚合渠道模型映射）

        Args:
            model_name: 模型名称（可能包含聚合渠道前缀）

        Returns:
            模型配置字典
        """
        # 1. 优先从 unified_llm_service 获取（Requirements: 4.6）
        try:
            from app.services.unified_llm_service import unified_llm_service
            merged = _run_async(unified_llm_service.get_model_config(model_name))

            if merged is not None:
                logger.info(f"🔍 [UnifiedLLMService] 找到模型配置: {model_name}")

                # 将 MergedModelConfig 的 features/roles 字符串转换为枚举
                features_enum = []
                for feature_str in (merged.features or []):
                    try:
                        features_enum.append(ModelFeature(feature_str))
                    except ValueError:
                        logger.warning(f"⚠️ 未知的特性值: {feature_str}")

                roles_enum = []
                for role_str in (merged.suitable_roles or []):
                    try:
                        roles_enum.append(ModelRole(role_str))
                    except ValueError:
                        logger.warning(f"⚠️ 未知的角色值: {role_str}")

                if not roles_enum:
                    roles_enum = [ModelRole.BOTH]

                logger.info(f"📊 [UnifiedLLMService配置] {model_name}: features={features_enum}, roles={roles_enum}")

                return {
                    "model_name": merged.model_name,
                    "capability_level": merged.capability_level,
                    "suitable_roles": roles_enum,
                    "features": features_enum,
                    "recommended_depths": DEFAULT_MODEL_CAPABILITIES.get(
                        model_name, {}
                    ).get("recommended_depths", ["快速", "基础", "标准"]),
                    "performance_metrics": DEFAULT_MODEL_CAPABILITIES.get(
                        model_name, {}
                    ).get("performance_metrics", None),
                }

        except Exception as e:
            logger.warning(f"从 unified_llm_service 读取模型信息失败: {e}", exc_info=True)

        # 2. 从默认映射表读取（直接匹配）
        if model_name in DEFAULT_MODEL_CAPABILITIES:
            return DEFAULT_MODEL_CAPABILITIES[model_name]

        # 3. 尝试聚合渠道模型映射
        provider, original_model = self._parse_aggregator_model_name(model_name)
        if original_model and original_model != model_name:
            if original_model in DEFAULT_MODEL_CAPABILITIES:
                logger.info(f"🔄 聚合渠道模型映射: {model_name} -> {original_model}")
                config = DEFAULT_MODEL_CAPABILITIES[original_model].copy()
                config["model_name"] = model_name  # 保持原始模型名
                config["_mapped_from"] = original_model  # 记录映射来源
                return config

        # 4. 返回默认配置
        logger.warning(f"未找到模型 {model_name} 的配置，使用默认配置")
        return {
            "model_name": model_name,
            "capability_level": 2,
            "suitable_roles": [ModelRole.BOTH],
            "features": [ModelFeature.TOOL_CALLING],
            "recommended_depths": ["快速", "基础", "标准"],
            "performance_metrics": {"speed": 3, "cost": 3, "quality": 3}
        }
    
    def validate_model_pair(
        self,
        quick_model: str,
        deep_model: str,
        research_depth: str
    ) -> Dict[str, Any]:
        """
        验证模型对是否适合当前分析深度

        Args:
            quick_model: 快速分析模型名称
            deep_model: 深度分析模型名称
            research_depth: 研究深度（快速/基础/标准/深度/全面）

        Returns:
            验证结果字典，包含 valid, warnings, recommendations
        """
        logger.info(f"🔍 开始验证模型对: quick={quick_model}, deep={deep_model}, depth={research_depth}")

        requirements = ANALYSIS_DEPTH_REQUIREMENTS.get(research_depth, ANALYSIS_DEPTH_REQUIREMENTS["标准"])
        logger.info(f"🔍 分析深度要求: {requirements}")

        quick_config = self.get_model_config(quick_model)
        deep_config = self.get_model_config(deep_model)

        logger.info(f"🔍 快速模型配置: {quick_config}")
        logger.info(f"🔍 深度模型配置: {deep_config}")

        result = {
            "valid": True,
            "warnings": [],
            "recommendations": []
        }
        
        # 检查快速模型
        quick_level = quick_config["capability_level"]
        logger.info(f"🔍 检查快速模型能力等级: {quick_level} >= {requirements['quick_model_min']}?")
        if quick_level < requirements["quick_model_min"]:
            warning = f"⚠️ 快速模型 {quick_model} (能力等级{quick_level}) 低于 {research_depth} 分析的建议等级({requirements['quick_model_min']})"
            result["warnings"].append(warning)
            logger.warning(warning)

        # 检查快速模型角色适配
        quick_roles = quick_config.get("suitable_roles", [])
        logger.info(f"🔍 检查快速模型角色: {quick_roles}")
        if ModelRole.QUICK_ANALYSIS not in quick_roles and ModelRole.BOTH not in quick_roles:
            warning = f"💡 模型 {quick_model} 不是为快速分析优化的，可能影响数据收集效率"
            result["warnings"].append(warning)
            logger.warning(warning)

        # 检查快速模型是否支持工具调用
        quick_features = quick_config.get("features", [])
        logger.info(f"🔍 检查快速模型特性: {quick_features}")
        if ModelFeature.TOOL_CALLING not in quick_features:
            result["valid"] = False
            warning = f"❌ 快速模型 {quick_model} 不支持工具调用，无法完成数据收集任务"
            result["warnings"].append(warning)
            logger.error(warning)

        # 检查深度模型
        deep_level = deep_config["capability_level"]
        logger.info(f"🔍 检查深度模型能力等级: {deep_level} >= {requirements['deep_model_min']}?")
        if deep_level < requirements["deep_model_min"]:
            result["valid"] = False
            warning = f"❌ 深度模型 {deep_model} (能力等级{deep_level}) 不满足 {research_depth} 分析的最低要求(等级{requirements['deep_model_min']})"
            result["warnings"].append(warning)
            logger.error(warning)
            result["recommendations"].append(
                self._recommend_model("deep", requirements["deep_model_min"])
            )

        # 检查深度模型角色适配
        deep_roles = deep_config.get("suitable_roles", [])
        logger.info(f"🔍 检查深度模型角色: {deep_roles}")
        if ModelRole.DEEP_ANALYSIS not in deep_roles and ModelRole.BOTH not in deep_roles:
            warning = f"💡 模型 {deep_model} 不是为深度推理优化的，可能影响分析质量"
            result["warnings"].append(warning)
            logger.warning(warning)

        # 检查必需特性
        logger.info(f"🔍 检查必需特性: {requirements['required_features']}")
        for feature in requirements["required_features"]:
            if feature == ModelFeature.REASONING:
                deep_features = deep_config.get("features", [])
                logger.info(f"🔍 检查深度模型推理能力: {deep_features}")
                if feature not in deep_features:
                    warning = f"💡 {research_depth} 分析建议使用具有强推理能力的深度模型"
                    result["warnings"].append(warning)
                    logger.warning(warning)

        logger.info(f"🔍 验证结果: valid={result['valid']}, warnings={len(result['warnings'])}条")
        logger.info(f"🔍 警告详情: {result['warnings']}")

        return result
    
    def recommend_models_for_depth(
        self,
        research_depth: str
    ) -> Tuple[str, str]:
        """
        根据分析深度推荐合适的模型对
        
        Args:
            research_depth: 研究深度（快速/基础/标准/深度/全面）
            
        Returns:
            (quick_model, deep_model) 元组
        """
        requirements = ANALYSIS_DEPTH_REQUIREMENTS.get(research_depth, ANALYSIS_DEPTH_REQUIREMENTS["标准"])
        
        # 获取所有启用的模型（通过 unified_llm_service，Requirements: 4.6）
        try:
            from app.services.unified_llm_service import unified_llm_service
            available_merged = _run_async(unified_llm_service.get_available_models())
            # 将 MergedModelConfig 转换为兼容的属性访问对象
            enabled_models = available_merged
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            # 使用默认模型
            return self._get_default_models()
        
        if not enabled_models:
            logger.warning("没有启用的模型，使用默认配置")
            return self._get_default_models()
        
        # 筛选适合快速分析的模型
        quick_candidates = []
        for m in enabled_models:
            roles = getattr(m, 'suitable_roles', [ModelRole.BOTH])
            level = getattr(m, 'capability_level', 2)
            features = getattr(m, 'features', [])
            
            if (ModelRole.QUICK_ANALYSIS in roles or ModelRole.BOTH in roles) and \
               level >= requirements["quick_model_min"] and \
               ModelFeature.TOOL_CALLING in features:
                quick_candidates.append(m)
        
        # 筛选适合深度分析的模型
        deep_candidates = []
        for m in enabled_models:
            roles = getattr(m, 'suitable_roles', [ModelRole.BOTH])
            level = getattr(m, 'capability_level', 2)
            
            if (ModelRole.DEEP_ANALYSIS in roles or ModelRole.BOTH in roles) and \
               level >= requirements["deep_model_min"]:
                deep_candidates.append(m)
        
        # 按性价比排序（能力等级 vs 成本）
        quick_candidates.sort(
            key=lambda x: (
                getattr(x, 'capability_level', 2),
                -getattr(x, 'performance_metrics', {}).get("cost", 3) if getattr(x, 'performance_metrics', None) else 0
            ),
            reverse=True
        )
        
        deep_candidates.sort(
            key=lambda x: (
                getattr(x, 'capability_level', 2),
                getattr(x, 'performance_metrics', {}).get("quality", 3) if getattr(x, 'performance_metrics', None) else 0
            ),
            reverse=True
        )
        
        # 选择最佳模型
        quick_model = quick_candidates[0].model_name if quick_candidates else None
        deep_model = deep_candidates[0].model_name if deep_candidates else None
        
        # 如果没找到合适的，使用系统默认
        if not quick_model or not deep_model:
            return self._get_default_models()
        
        logger.info(
            f"🤖 为 {research_depth} 分析推荐模型: "
            f"quick={quick_model} (角色:快速分析), "
            f"deep={deep_model} (角色:深度推理)"
        )
        
        return quick_model, deep_model
    
    def _get_default_models(self) -> Tuple[str, str]:
        """获取默认模型对"""
        try:
            from app.services.unified_llm_service import unified_llm_service
            default_model = _run_async(unified_llm_service.get_default_model())
            if default_model:
                # 默认模型同时用于快速和深度分析
                logger.info(f"使用系统默认模型: {default_model.model_name}")
                return default_model.model_name, default_model.model_name
        except Exception as e:
            logger.error(f"从 unified_llm_service 获取默认模型失败: {e}")

        # 回退到硬编码默认值
        logger.warning("无法获取默认模型，使用硬编码默认值")
        return "qwen-turbo", "qwen-plus"
    
    def _recommend_model(self, model_type: str, min_level: int) -> str:
        """推荐满足要求的模型"""
        try:
            from app.services.unified_llm_service import unified_llm_service
            available_models = _run_async(unified_llm_service.get_available_models())
            for model in available_models:
                if model.capability_level >= min_level:
                    display_name = model.model_display_name or model.model_name
                    return f"建议使用: {display_name}"
        except Exception as e:
            logger.warning(f"推荐模型失败: {e}")
        
        return "建议升级模型配置"


# 单例
_model_capability_service = None


def get_model_capability_service() -> ModelCapabilityService:
    """获取模型能力服务单例"""
    global _model_capability_service
    if _model_capability_service is None:
        _model_capability_service = ModelCapabilityService()
    return _model_capability_service

