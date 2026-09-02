"""Skill_Local_POI —— 本地 POI 推荐。

多路召回 + 综合评分，生成差异化推荐方案。
高德 3 路结构化召回 + Tavily Web 3 路氛围召回，
Web 候选回查高德映射为真实 POI（6 套统一包），
再经 Claude 综合评分排序。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Tuple

from ..adapters.amap import AmapError, get_amap
from ..adapters.claude import ClaudeError, get_claude
from ..adapters.tavily import TavilyError, get_tavily
from ..core.logging import get_logger
from .base import BaseSkill, SkillResult, SkillStatus

logger = get_logger(__name__)


class LocalPOISkill(BaseSkill):
    """本地 POI 推荐 Skill。"""

    name = "local_poi"
    description = (
        "本地兴趣点（POI）搜索与推荐服务。"
        "根据用户意图和画像，推荐餐厅、咖啡店、商场、景点等地点。"
        "支持关键词搜索、周边搜索、POI详情查询，"
        "并结合用户画像进行个性化评分与推荐。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["poi_recommend", "poi_resolve", "poi_compare", "poi_search"],
                "default": "poi_recommend",
                "description": "执行的操作类型",
            },
            "intent": {
                "type": "string",
                "description": "搜索意图（dining/coffee/shopping/leisure/parking/custom）",
            },
            "keyword": {
                "type": "string",
                "description": "搜索关键词（用户原话）",
            },
            "center_position": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number"},
                    "lon": {"type": "number"},
                },
                "required": ["lat", "lon"],
                "description": "搜索中心点位置（通常是当前车辆位置）",
            },
            "radius_km": {
                "type": "number",
                "default": 3.0,
                "description": "搜索半径（公里）",
            },
            "limit": {
                "type": "integer",
                "default": 3,
                "description": "推荐数量",
            },
            "poi_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "POI ID 列表（对比/详情用）",
            },
            "candidates_count": {
                "type": "integer",
                "default": 5,
                "description": "消歧候选数量",
            },
            "user_profile": {
                "type": "object",
                "description": "用户画像（用于个性化推荐）",
            },
            "weather": {
                "type": "object",
                "properties": {
                    "weather": {"type": "string"},
                    "windpower": {"type": "string"},
                },
                "description": "天气信息（用于过滤户外场所）",
            },
            "city": {
                "type": "string",
                "default": "上海",
                "description": "城市名称",
            },
        },
        "required": ["action", "center_position"],
    }
    gcp_dependencies = [
        "vehicle.position",
        "user_profile",
        "weather.live",
        "time.time_bucket",
        "in_cabin.passengers",
    ]

    # 意图 → 搜索关键词组映射
    INTENT_KEYWORDS = {
        "dining": ["餐厅", "美食", "吃饭", "餐厅推荐"],
        "drinks": ["酒吧", "清吧", "小酒馆", "喝酒", "小酌"],
        "coffee": ["咖啡", "咖啡厅", "咖啡店"],
        "shopping": ["商场", "购物中心", "购物"],
        "leisure": ["景点", "休闲娱乐", "游玩"],
        "parking": ["停车场"],
        "custom": [],
    }

    # 意图 → 高德 POI 类型编码
    INTENT_TYPES = {
        "dining": "050000",        # 餐饮服务
        "drinks": "050500",        # 酒吧（餐饮服务 > 冷饮/酒吧类）
        "coffee": "050900",        # 咖啡厅
        "shopping": "060000",       # 购物服务
        "leisure": "110000",       # 风景名胜
        "parking": "150000",       # 交通设施服务（含停车场）
        "custom": "",
    }

    async def execute(
        self,
        params: Dict[str, Any],
        gcp_slice: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        action = params.get("action", "poi_recommend")

        try:
            if action == "poi_recommend":
                return await self._poi_recommend(params, gcp_slice)
            elif action == "poi_resolve":
                return await self._poi_resolve(params)
            elif action == "poi_compare":
                return await self._poi_compare(params)
            elif action == "poi_search":
                return await self._poi_search(params)
            else:
                return SkillResult.error(f"unknown action: {action}")
        except AmapError as e:
            logger.error("local_poi amap error: %s", e)
            return SkillResult.error(str(e), "POI 搜索服务暂时不可用")
        except Exception as e:
            logger.exception("local_poi error: %s", e)
            return SkillResult.error(str(e), "POI 搜索出错")

    # ==================== poi_recommend ====================

    async def _poi_recommend(
        self, params: Dict[str, Any], gcp_slice: Dict[str, Any]
    ) -> SkillResult:
        """核心推荐方法：多路召回 → 去重合并 → 评分排序 → Top N。"""
        intent = params.get("intent", "custom")
        keyword = params.get("keyword", "")
        center = params["center_position"]
        radius_m = int(params.get("radius_km", 3.0) * 1000)
        limit = params.get("limit", 3)
        city = params.get("city", "上海")

        # 从 GCP 获取用户画像和天气
        user_profile = params.get("user_profile") or gcp_slice.get("user_profile", {})
        weather = params.get("weather") or {}
        if not weather:
            try:
                weather = gcp_slice["weather"]["live"]
            except (KeyError, TypeError):
                weather = {}

        # 生成搜索关键词组
        search_terms = self._generate_search_terms(intent, keyword, user_profile)
        if not search_terms:
            search_terms = [keyword or "推荐"]

        # 提取多搜索中心：主中心（当前位置）+ 用户常去区域副中心
        extra_centers = self._extract_frequent_centers(user_profile, intent, center)
        social_radius = self._get_social_radius(user_profile)
        sub_radius_m = int(social_radius * 0.8 * 1000)  # 副中心半径 = social_radius_km * 0.8（提升常去区域覆盖）

        # 获取时间段信息
        time_bucket = ""
        try:
            from ..core.logging import get_logger as _gl
            # 从 gcp_slice 中获取时间
            time_ctx = gcp_slice.get("time", {}) if isinstance(gcp_slice, dict) else {}
            time_bucket = time_ctx.get("time_bucket", "") if isinstance(time_ctx, dict) else ""
        except Exception:
            pass

        amap = get_amap()

        # ========== 多路召回（高德 3 路） ==========
        all_candidates: Dict[str, Dict[str, Any]] = {}  # id → poi dict

        # 构建搜索中心列表：[(center_dict, radius_m, center_label, is_primary)]
        search_centers: List[Tuple[Dict[str, float], int, str, bool]] = [
            (center, radius_m, "current", True),
        ]
        for ec in extra_centers:
            search_centers.append((ec["pos"], sub_radius_m, ec["label"], False))

        # 路1：关键词搜索（精确匹配） — 所有中心点
        async def _recall_text(
            ctr: Dict[str, float], r_m: int, label: str, is_primary: bool,
        ) -> None:
            offset = 10 if is_primary else 6
            for term in search_terms[:2 if is_primary else 1]:
                try:
                    pois = await amap.place_text(
                        keyword=term,
                        city=city,
                        citylimit=True,
                        offset=offset,
                        extensions="all",
                        location=f"{ctr['lon']},{ctr['lat']}",
                        radius=r_m,
                    )
                    for poi in pois:
                        poi_id = poi.get("id", "")
                        if poi_id and poi_id not in all_candidates:
                            poi["_source"] = "amap_text"
                            poi["_search_term"] = term
                            poi["_center_label"] = label
                            all_candidates[poi_id] = poi
                except Exception as e:
                    logger.warning("amap place_text failed at %s: %s", label, e)

        # 路2：周边搜索 + 类型过滤 — 所有中心点
        async def _recall_around_type(
            ctr: Dict[str, float], r_m: int, label: str, is_primary: bool,
        ) -> None:
            poi_type = self.INTENT_TYPES.get(intent, "")
            if not poi_type:
                return
            offset = 15 if is_primary else 8
            try:
                pois = await amap.place_around(
                    location=(ctr["lon"], ctr["lat"]),
                    types=poi_type,
                    radius=r_m,
                    offset=offset,
                    extensions="all",
                    sort="distance",
                )
                for poi in pois:
                    poi_id = poi.get("id", "")
                    if poi_id and poi_id not in all_candidates:
                        poi["_source"] = "amap_around"
                        poi["_center_label"] = label
                        all_candidates[poi_id] = poi
            except Exception as e:
                logger.warning("amap place_around failed at %s: %s", label, e)

        # 并发执行所有中心的召回
        recall_tasks = []
        for ctr, r_m, label, is_primary in search_centers:
            recall_tasks.append(_recall_text(ctr, r_m, label, is_primary))
            recall_tasks.append(_recall_around_type(ctr, r_m, label, is_primary))
        await asyncio.gather(*recall_tasks, return_exceptions=True)

        # 路3：关键词 + 周边排序 — 仅主中心（避免 API 调用过多）
        try:
            if keyword:
                pois = await amap.place_around(
                    location=(center["lon"], center["lat"]),
                    keyword=keyword,
                    radius=radius_m,
                    offset=10,
                    extensions="all",
                    sort="weight",
                )
                for poi in pois:
                    poi_id = poi.get("id", "")
                    if poi_id and poi_id not in all_candidates:
                        poi["_source"] = "amap_around_kw"
                        poi["_center_label"] = "current"
                        all_candidates[poi_id] = poi
        except Exception as e:
            logger.warning("amap place_around keyword failed: %s", e)

        # ========== 多路召回（Tavily Web Search，路4-6） ==========
        # Web 搜索先作为"氛围召回"，然后映射到高德 POI，形成"6 套统一包"。
        web_results_all: List[Dict[str, Any]] = []
        try:
            tavily = get_tavily()
            if tavily.api_key:
                # 构造 web search 查询词
                intent_label_map = {
                    "dining": "餐厅推荐",
                    "drinks": "酒吧推荐",
                    "coffee": "咖啡店推荐",
                    "shopping": "商场推荐",
                    "leisure": "景点推荐",
                    "parking": "停车场",
                    "custom": "推荐",
                }
                intent_label = intent_label_map.get(intent, "推荐")
                base_query = keyword or intent_label
                web_queries = [
                    f"{city}{base_query} 口碑好的 推荐",
                    f"{city}附近{intent_label} 必去 评价",
                ]

                seen_web_names = set()
                for wquery in web_queries[:2]:
                    try:
                        web_results = await tavily.search_poi_web(
                            keyword=wquery,
                            city="",  # 已含城市
                            max_results=5,
                        )
                        for wpoi in web_results:
                            name = wpoi.get("name", "")
                            if name and name not in seen_web_names:
                                seen_web_names.add(name)
                                wpoi["_search_term"] = wquery
                                web_results_all.append(wpoi)
                    except Exception as e:
                        logger.warning("tavily search query '%s' failed: %s", wquery[:50], e)

                if web_results_all:
                    logger.info("tavily web search returned %d unique results", len(web_results_all))
        except (TavilyError, Exception) as e:
            logger.warning("tavily web search unavailable: %s", e)

        # ---------- Web → 高德 POI 映射（6 套统一包） ----------
        # 把每条 Web 候选回查高德，映射为真实 POI（有坐标、地址、电话等）
        # 高德 3 路结构化 + Web 3 路映射 = 6 套统一候选，再进入后续排序/评分
        web_mapped_count = 0
        if web_results_all and amap:
            mapped_pois = await self._map_web_to_amap(
                web_results=web_results_all,
                amap=amap,
                city=city,
                center=center,
                radius_m=radius_m,
                existing_ids=set(all_candidates.keys()),
                limit=5,  # 最多映射 5 个，控制 API 调用量
            )
            for poi in mapped_pois:
                pid = poi.get("id", "")
                if pid and pid not in all_candidates:
                    all_candidates[pid] = poi
                    web_mapped_count += 1
            if web_mapped_count > 0:
                logger.info("web→amap mapped %d candidates", web_mapped_count)

        if not all_candidates:
            return SkillResult.no_result(
                f"在{radius_m/1000:.0f}公里范围内没有找到合适的{_intent_label(intent)}推荐，建议扩大搜索范围。"
            )

        # ========== 天气过滤（恶劣天气屏蔽户外场所） ==========
        candidates_list = list(all_candidates.values())
        filtered = self._weather_filter(candidates_list, weather)
        if not filtered:
            # 过滤后没有了，使用原始结果
            filtered = candidates_list

        # ========== 时间适配过滤（过滤明显不合适的时间段POI） ==========
        time_bucket = params.get("time_bucket", time_bucket)
        filtered = self._time_filter(filtered, intent, time_bucket)
        if not filtered:
            filtered = candidates_list  # 兜底

        # 获取场景信息
        companion_type = params.get("companion_type", "")
        occasion = params.get("occasion", "")

        # ========== 标准化候选格式 ==========
        normalized = [self._normalize_poi(p, center) for p in filtered]

        # ========== 综合评分（Claude 做个性化推荐） ==========
        if len(normalized) > limit:
            try:
                recommended, reasoning = await self._ai_score_and_rank(
                    candidates=normalized,
                    intent=intent,
                    keyword=keyword,
                    user_profile=user_profile,
                    weather=weather,
                    center=center,
                    limit=limit,
                    time_bucket=time_bucket,
                    companion_type=companion_type,
                    occasion=occasion,
                )
            except ClaudeError as e:
                logger.warning("AI scoring failed, fallback to distance sort: %s", e)
                # 降级：优先有坐标的，然后按距离排序
                normalized.sort(key=lambda x: (not x.get("has_location", False), x.get("distance_m", 99999)))
                recommended = normalized[:limit]
                reasoning = f"按距离由近到远推荐了{len(recommended)}个{_intent_label(intent)}。"
        else:
            # 候选数量少，确保有坐标的排在前面
            normalized.sort(key=lambda x: (not x.get("has_location", False), x.get("distance_m", 99999)))
            recommended = normalized[:limit]
            reasoning = f"找到{len(recommended)}个候选，全部推荐。"

        # ========== 构造结果 ==========
        result = {
            "candidates": normalized,
            "recommended": recommended,
            "total_found": len(normalized),
            "reasoning": reasoning,
            "intent": intent,
            "search_terms_used": search_terms,
            "center": center,
            "radius_km": radius_m / 1000,
        }

        # 生成播报文案
        dest_labels = "、".join([r.get("name", "") for r in recommended[:3]])
        top = recommended[0] if recommended else {}
        if top.get("has_location") and top.get("distance_km", 0) < 100:
            top_dist_km = top.get('distance_km', 0)
            top_dist_str = f"{top_dist_km:.1f}公里" if top_dist_km >= 1 else f"{int(top_dist_km*1000)}米"
            message = f"为您找到{len(recommended)}个{_intent_label(intent)}推荐：{dest_labels}。其中{top.get('name', '第一个')}距离最近，约{top_dist_str}。"
        else:
            # 第一个没有精确位置信息
            message = f"为您找到{len(recommended)}个{_intent_label(intent)}推荐：{dest_labels}。综合评价最高的是{top.get('name', '第一个')}。"

        return SkillResult.success(result, message)

    # ==================== poi_resolve ====================

    async def _poi_resolve(self, params: Dict[str, Any]) -> SkillResult:
        """POI 消歧：当搜索词模糊时，列出候选供确认。"""
        keyword = params.get("keyword", "")
        center = params.get("center_position") or {"lat": 31.2359, "lon": 121.4996}
        count = params.get("candidates_count", 5)
        city = params.get("city", "上海")

        if not keyword:
            return SkillResult.error("keyword is required")

        amap = get_amap()
        radius_m = int(params.get("radius_km", 50.0) * 1000)  # 默认半径 50km

        try:
            kw = dict(
                keyword=keyword,
                city=city,
                citylimit=True,
                offset=count,
                extensions="base",
            )
            if center and center.get("lat"):
                kw["location"] = f"{center['lon']},{center['lat']}"
                kw["radius"] = radius_m
            pois = await amap.place_text(**kw)
        except AmapError as e:
            return SkillResult.error(str(e), "搜索失败")

        if not pois:
            return SkillResult.no_result(f"没有找到与「{keyword}」相关的地点")

        # 计算每个 POI 的距离（place_text 返回的 distance 可能为空）
        center_lat = center.get("lat", 0) if center else 0
        center_lon = center.get("lon", 0) if center else 0
        candidates = []
        for p in pois[:count]:
            dist = 0.0
            dist_str = p.get("distance", "")
            if dist_str and str(dist_str).strip():
                try:
                    dist = float(dist_str)
                except (ValueError, TypeError):
                    dist = 0.0
            # 如果 distance 为空，用 haversine 计算
            if dist <= 0 and center_lat and center_lon:
                loc = p.get("location", "")
                if loc:
                    try:
                        lon, lat = map(float, loc.split(","))
                        dist = self._haversine_m(center_lat, center_lon, lat, lon)
                    except (ValueError, TypeError):
                        dist = 0.0
            candidates.append({
                "id": p.get("id", ""),
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "category": p.get("type", ""),
                "distance": round(dist, 0),
                "location": p.get("location", ""),
            })

        result = {
            "keyword": keyword,
            "candidates": candidates,
            "total": len(pois),
        }

        names = "、".join([c["name"] for c in candidates[:3]])
        message = f"找到{len(candidates)}个和「{keyword}」相关的地点：{names}，请问您想选择哪一个？"

        return SkillResult.success(result, message)

    # ==================== poi_compare ====================

    async def _poi_compare(self, params: Dict[str, Any]) -> SkillResult:
        """POI 对比：获取详情并生成对比列表。"""
        poi_ids = params.get("poi_ids", [])
        if not poi_ids:
            return SkillResult.error("poi_ids is required")

        amap = get_amap()
        details = []

        for pid in poi_ids:
            try:
                detail = await amap.place_detail(pid)
                if detail:
                    details.append(detail)
            except Exception as e:
                logger.warning("poi detail failed for %s: %s", pid, e)

        if not details:
            return SkillResult.no_result("未找到 POI 详情")

        comparison = [
            {
                "id": d.get("id", ""),
                "name": d.get("name", ""),
                "address": d.get("address", ""),
                "rating": d.get("rating", ""),
                "cost": d.get("cost", ""),
                "tel": d.get("tel", ""),
                "type": d.get("type", ""),
                "distance": d.get("distance", ""),
            }
            for d in details
        ]

        result = {
            "comparison": comparison,
            "count": len(comparison),
        }

        names = "、".join([c["name"] for c in comparison])
        message = f"已为您对比{len(comparison)}个地点：{names}。"

        return SkillResult.success(result, message)

    # ==================== poi_search ====================

    async def _poi_search(self, params: Dict[str, Any]) -> SkillResult:
        """简单 POI 搜索（直接返回搜索结果，不做推荐）。"""
        keyword = params.get("keyword", "")
        center = params["center_position"]
        radius_m = int(params.get("radius_km", 3.0) * 1000)
        city = params.get("city", "上海")
        limit = params.get("limit", 10)

        if not keyword:
            return SkillResult.error("keyword is required")

        amap = get_amap()
        try:
            pois = await amap.place_around(
                location=(center["lon"], center["lat"]),
                keyword=keyword,
                radius=radius_m,
                offset=limit,
                extensions="base",
                sort="distance",
            )
        except AmapError as e:
            return SkillResult.error(str(e), "搜索失败")

        if not pois:
            return SkillResult.no_result(f"没有找到「{keyword}」相关的地点")

        results = [self._normalize_poi(p, center) for p in pois[:limit]]

        result = {
            "results": results,
            "total": len(pois),
            "keyword": keyword,
        }

        message = f"找到{len(results)}个「{keyword}」相关地点。"
        return SkillResult.success(result, message)

    # ==================== 辅助方法 ====================

    @staticmethod
    def _generate_search_terms(
        intent: str, keyword: str, user_profile: Dict[str, Any]
    ) -> List[str]:
        """根据意图、关键词和用户画像生成差异化搜索词组。"""
        terms: List[str] = []

        # 基础关键词
        base_terms = LocalPOISkill.INTENT_KEYWORDS.get(intent, [])
        terms.extend(base_terms[:2])

        # 用户画像补充（餐饮偏好等）
        if intent == "dining":
            lifestyle = user_profile.get("lifestyle_preferences", {}) if user_profile else {}
            cuisines = lifestyle.get("cuisine_types", []) if isinstance(lifestyle, dict) else []
            dining_tags = lifestyle.get("dining", []) if isinstance(lifestyle, dict) else []
            if cuisines:
                terms.append(f"{cuisines[0]}餐厅")
            elif dining_tags:
                terms.append(dining_tags[0])

        # 显式关键词优先
        if keyword:
            # 如果用户给了关键词，放在最前面
            terms.insert(0, keyword)

        # 去重并限制数量
        seen = set()
        unique_terms = []
        for t in terms:
            if t and t not in seen:
                seen.add(t)
                unique_terms.append(t)

        return unique_terms[:5]

    @staticmethod
    def _get_social_radius(user_profile: Dict[str, Any]) -> float:
        """从用户画像读取社交半径（km），默认 5km。"""
        if not user_profile or not isinstance(user_profile, dict):
            return 5.0
        travel = user_profile.get("travel_preferences", {})
        if isinstance(travel, dict):
            radius = travel.get("social_radius_km", 5.0)
            try:
                return float(radius)
            except (ValueError, TypeError):
                pass
        return 5.0

    @staticmethod
    def _extract_frequent_centers(
        user_profile: Dict[str, Any],
        intent: str,
        center: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """从用户画像中提取常去区域作为额外搜索中心点。

        返回最多 2 个副中心，每个含: {pos: {lat, lon}, label: str}
        - 优先根据意图匹配相关 tag 的常去 POI
        - 加上家/办公室（如果不在当前位置附近且与意图相关）
        - 过滤掉距离当前位置 <1km 的（避免重复召回）
        """
        if not user_profile or not isinstance(user_profile, dict):
            return []

        candidates: List[Dict[str, Any]] = []
        center_lat = center.get("lat", 0)
        center_lon = center.get("lon", 0)

        # 意图 → 偏好的 tag 映射
        intent_tag_priority = {
            "dining": ["leisure", "mall", "home"],
            "drinks": ["leisure", "mall", "home"],
            "coffee": ["leisure", "mall", "office"],
            "shopping": ["mall", "leisure"],
            "leisure": ["leisure", "park"],
            "parking": ["mall", "office", "home"],
            "custom": ["leisure", "mall"],
        }
        preferred_tags = set(intent_tag_priority.get(intent, ["leisure", "mall"]))

        def _too_close(lat: float, lon: float) -> bool:
            """判断是否离当前中心太近（<1km）。"""
            if center_lat == 0 or lat == 0:
                return False
            return LocalPOISkill._haversine_m(center_lat, center_lon, lat, lon) < 1000

        def _try_add(lat_val: Any, lon_val: Any, label: str, score: int) -> None:
            try:
                lat_f = float(lat_val)
                lon_f = float(lon_val)
            except (ValueError, TypeError):
                return
            if lat_f <= 0 or lon_f <= 0:
                return
            if _too_close(lat_f, lon_f):
                return
            candidates.append({
                "pos": {"lat": lat_f, "lon": lon_f},
                "label": label,
                "score": score,
            })

        # 1. frequent_pois：按 tag 相关性排序
        travel = user_profile.get("travel_preferences", {})
        if isinstance(travel, dict):
            for poi in travel.get("frequent_pois", []) or []:
                if not isinstance(poi, dict):
                    continue
                tag = str(poi.get("tag", ""))
                name = str(poi.get("name", ""))
                # 优先取 location 对象中的坐标，否则取 poi 直接的 lat/lon
                loc = poi.get("location")
                lat, lon = 0, 0
                if isinstance(loc, dict) and loc.get("lat") and loc.get("lon"):
                    lat = loc.get("lat", 0)
                    lon = loc.get("lon", 0)
                if not lat or not lon:
                    lat = poi.get("lat", 0)
                    lon = poi.get("lon", 0)

                # 跳过 home/office 标签（后面单独处理，避免重复）
                if tag in ("home", "office"):
                    continue

                # 按 tag 匹配度打分：匹配 preferred_tags 给高分，
                # 不相关 tag（airport 等）给 0 分排除
                irrelevant_tags = {"airport"}
                if tag in preferred_tags:
                    s = 3
                elif tag in irrelevant_tags:
                    s = 0
                else:
                    s = 1
                _try_add(lat, lon, name or tag, s)

        # 2. 家 / 办公室位置
        home_loc = user_profile.get("home_location", {})
        office_loc = user_profile.get("office_location", {})
        if isinstance(home_loc, dict):
            _try_add(
                home_loc.get("lat", 0), home_loc.get("lon", 0),
                str(user_profile.get("home_address", "家附近")) or "家附近",
                2 if "home" in preferred_tags else 0,
            )
        if isinstance(office_loc, dict):
            _try_add(
                office_loc.get("lat", 0), office_loc.get("lon", 0),
                str(user_profile.get("office_address", "公司附近")) or "公司附近",
                2 if "office" in preferred_tags else 0,
            )

        # 按 score 降序排列，去重相似位置（同一区域只取一个），最多 2 个
        candidates.sort(key=lambda x: x["score"], reverse=True)
        selected: List[Dict[str, Any]] = []
        seen_positions: List[Tuple[float, float]] = []
        for c in candidates:
            if c["score"] <= 0:
                continue
            lat_c = c["pos"]["lat"]
            lon_c = c["pos"]["lon"]
            # 去重：两个副中心之间距离 <1km 视为同一区域
            too_close_to_other = any(
                LocalPOISkill._haversine_m(lat_c, lon_c, slat, slon) < 1000
                for slat, slon in seen_positions
            )
            if too_close_to_other:
                continue
            selected.append(c)
            seen_positions.append((lat_c, lon_c))
            if len(selected) >= 3:
                break

        logger.info(
            "extracted %d extra search centers for intent=%s: %s",
            len(selected), intent,
            [c["label"] for c in selected],
        )
        return selected

    @staticmethod
    def _time_filter(
        candidates: List[Dict[str, Any]],
        intent: str,
        time_bucket: str,
    ) -> List[Dict[str, Any]]:
        """根据时间段过滤明显不合适的 POI。

        规则：
        - 深夜/凌晨（night/late_night）：过滤早餐店、商场、景点；保留酒吧/夜宵
        - 上午（morning/late_morning）：过滤酒吧/夜场；保留咖啡/早餐
        - 其他时段不做过滤
        """
        if not time_bucket or not candidates:
            return candidates

        # 深夜/凌晨：过滤商场、景点、咖啡厅
        night_buckets = {"night", "late_night"}
        # 上午：过滤酒吧
        morning_buckets = {"early_morning", "morning", "late_morning"}

        filtered = []
        for poi in candidates:
            name = poi.get("name", "")
            typ = poi.get("type", "") or ""
            typecode = poi.get("typecode", "") or ""
            combined = f"{name}{typ}{typecode}"

            if time_bucket in night_buckets:
                # 深夜过滤词
                night_exclude = ["商场", "购物中心", "百货", "景点", "风景区", "公园",
                                "博物馆", "展览", "咖啡", "早餐", "早茶"]
                if intent in ("drinks", "leisure"):
                    # 喝酒/休闲场景不做过滤
                    filtered.append(poi)
                    continue
                if any(kw in combined for kw in night_exclude):
                    continue
            elif time_bucket in morning_buckets:
                # 上午过滤酒吧/夜场
                morning_exclude = ["酒吧", "酒馆", "清吧", "夜店", "KTV", "夜总会"]
                if intent == "coffee":
                    filtered.append(poi)
                    continue
                if any(kw in combined for kw in morning_exclude):
                    continue

            filtered.append(poi)

        return filtered if filtered else candidates

    @staticmethod
    def _weather_filter(
        candidates: List[Dict[str, Any]], weather: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """恶劣天气过滤户外场所。"""
        if not weather:
            return candidates

        w = str(weather.get("weather", ""))
        wind = str(weather.get("windpower", ""))

        # 判断是否为恶劣天气
        is_bad_weather = (
            any(k in w for k in ["雨", "雪", "雷", "暴", "雾", "霾", "沙尘"])
            or False
        )
        # 风力判断
        try:
            wind_num = int(wind.replace("级", "").replace("-", "").split("~")[-1])
            is_windy = wind_num >= 5
        except (ValueError, IndexError):
            is_windy = False

        if not is_bad_weather and not is_windy:
            return candidates

        # 过滤户外类型（简单关键词过滤）
        outdoor_keywords = ["户外", "露台", "公园", "景区", "沙滩", "登山", "露营", "徒步"]
        filtered = []
        for poi in candidates:
            name = poi.get("name", "")
            typ = poi.get("type", "")
            is_outdoor = any(
                kw in name or kw in typ
                for kw in outdoor_keywords
            )
            if not is_outdoor:
                filtered.append(poi)

        return filtered if filtered else candidates

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """计算两点间的大圆距离（米）。"""
        import math
        R = 6371000.0  # 地球半径（米）
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = (math.sin(dlat / 2) ** 2
             + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
             * math.sin(dlon / 2) ** 2)
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return R * c

    @staticmethod
    async def _map_web_to_amap(
        web_results: List[Dict[str, Any]],
        amap: Any,
        city: str,
        center: Dict[str, float],
        radius_m: int,
        existing_ids: set,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """将 Web 搜索结果映射到高德 POI。

        对每条 Web 候选，用其名称做关键词搜索回查高德，
        取最佳匹配（第一个结果），用高德 POI 数据为主，
        附加 Web 来源的口碑信息（url、content、score）。

        返回映射成功的高德 POI 列表（已标记 _source="tavily_mapped"）。
        """
        if not web_results:
            return []

        # 取 top N 个 web 结果做映射（控制 API 调用数）
        top_results = web_results[:limit]
        center_str = f"{center['lon']},{center['lat']}"

        async def _map_one(wpoi: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            name = wpoi.get("name", "")
            if not name:
                return None
            try:
                # 用名称回查高德，取前 3 个候选，匹配最佳
                pois = await amap.place_text(
                    keyword=name,
                    city=city,
                    citylimit=True,
                    offset=3,
                    extensions="all",
                    location=center_str,
                    radius=radius_m,
                )
                if not pois:
                    return None
                # 取第一个作为最佳匹配（按相关度排序）
                best = pois[0]
                pid = best.get("id", "")
                if not pid or pid in existing_ids:
                    return None
                # 用高德 POI 为主，附加 web 信号字段
                best["_source"] = "tavily_mapped"
                best["_web_url"] = wpoi.get("url", "")
                best["_web_content"] = wpoi.get("content", "")
                best["_web_score"] = wpoi.get("score", 0.0)
                best["_web_search_term"] = wpoi.get("_search_term", "")
                best["_web_mention_count"] = 1
                return best
            except Exception as e:
                logger.warning("web→amap mapping failed for '%s': %s", name[:30], e)
                return None

        # 并发映射，控制并发数（最多 3 个并发）
        sem = asyncio.Semaphore(3)

        async def _throttled(wpoi):
            async with sem:
                return await _map_one(wpoi)

        tasks = [_throttled(w) for w in top_results]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        mapped = [r for r in results if r is not None]
        return mapped

    @staticmethod
    def _normalize_poi(poi: Dict[str, Any], center: Dict[str, float]) -> Dict[str, Any]:
        """标准化 POI 数据格式。"""
        location_str = poi.get("location", "")
        lat = 0.0
        lon = 0.0
        if location_str:
            try:
                parts = location_str.split(",")
                lon = float(parts[0])
                lat = float(parts[1])
            except (ValueError, IndexError):
                pass

        # 获取距离：优先使用 API 返回的 distance，没有则用 haversine 估算
        distance_str = poi.get("distance", "")
        distance_m = 0.0
        if distance_str and str(distance_str).strip():
            try:
                distance_m = float(distance_str)
            except (ValueError, TypeError):
                distance_m = 0.0

        # 如果 distance 为 0 且有中心坐标和 POI 坐标，则计算直线距离
        if distance_m <= 0 and lat > 0 and lon > 0 and center.get("lat", 0) > 0:
            distance_m = LocalPOISkill._haversine_m(
                center["lat"], center["lon"], lat, lon
            )

        # v5 API 中 rating/biz_ext 字段可能为空，统一处理
        rating = poi.get("rating", "")
        biz_ext = poi.get("biz_ext", {}) or {}
        if not rating and isinstance(biz_ext, dict):
            rating = biz_ext.get("rating", "")

        cost = poi.get("cost", "")
        if not cost and isinstance(biz_ext, dict):
            cost = biz_ext.get("cost", "")

        # web 来源字段处理
        raw_source = poi.get("_source", "amap")
        is_raw_web = raw_source == "tavily_web"  # 未映射的原始 web 候选（降级用）
        is_mapped = raw_source == "tavily_mapped"  # 已映射到高德的 web 候选

        if is_mapped:
            web_url = poi.get("_web_url", "")
            web_content = poi.get("_web_content", "")
            web_score = poi.get("_web_score", 0.0)
        elif is_raw_web:
            web_url = poi.get("url", "")
            web_content = poi.get("content", "")
            web_score = poi.get("score", 0.0)
        else:
            web_url = ""
            web_content = ""
            web_score = 0.0

        # 未映射的原始 web 来源没有具体位置信息，标记为未知距离
        if is_raw_web and distance_m <= 0:
            distance_m = 99999.0  # 排到最后

        return {
            "id": poi.get("id", ""),
            "name": poi.get("name", ""),
            "address": poi.get("address", ""),
            "category": poi.get("type", ""),
            "typecode": poi.get("typecode", ""),
            "location": {"lat": lat, "lon": lon},
            "distance_m": round(distance_m, 0),
            "distance_km": round(distance_m / 1000, 2),
            "tel": poi.get("tel", ""),
            "rating": str(rating) if rating else "",
            "cost": str(cost) if cost else "",
            "business_area": poi.get("business_area", ""),
            "photos": poi.get("photos", []),
            "source": raw_source,
            "web_url": web_url,
            "web_content": web_content[:200] if web_content else "",
            "web_score": web_score,
            "has_location": lat > 0 and lon > 0,
            "_center_label": poi.get("_center_label", "current"),
        }

    async def _ai_score_and_rank(
        self,
        candidates: List[Dict[str, Any]],
        intent: str,
        keyword: str,
        user_profile: Dict[str, Any],
        weather: Dict[str, Any],
        center: Dict[str, float],
        limit: int,
        time_bucket: str = "",
        companion_type: str = "",
        occasion: str = "",
    ) -> Tuple[List[Dict[str, Any]], str]:
        """用 Claude 做综合评分和排序，返回 Top N 和推荐理由。

        评分按 5 层漏斗模型：
        1. 空间可达（距离合理）
        2. 时间适配（当前时段适合去）
        3. 用户匹配（画像+历史常去区域）
        4. 质量口碑（评分/口碑）
        5. 场景意图（同行人/场合匹配）
        """
        if len(candidates) <= limit:
            return candidates, "候选数量较少，全部推荐。"

        claude = get_claude()

        # 提取常去区域名称（用于评分时参考）
        frequent_area_labels = []
        if user_profile and isinstance(user_profile, dict):
            travel = user_profile.get("travel_preferences", {})
            if isinstance(travel, dict):
                for fp in (travel.get("frequent_pois", []) or []):
                    if isinstance(fp, dict) and fp.get("name"):
                        tag = str(fp.get("tag", ""))
                        if tag not in ("home", "office"):
                            frequent_area_labels.append(str(fp["name"]))
            if user_profile.get("home_address"):
                frequent_area_labels.append("家")
            if user_profile.get("office_address"):
                frequent_area_labels.append("公司")

        # 构造候选摘要（精简，节省 token）
        candidates_summary = []
        for i, c in enumerate(candidates[:20]):  # 最多 20 个候选
            entry: Dict[str, Any] = {
                "index": i,
                "name": c.get("name", ""),
                "category": c.get("category", ""),
                "distance_km": c.get("distance_km", 0),
                "rating": c.get("rating", ""),
                "cost": c.get("cost", ""),
                "address": c.get("address", "")[:50],
            }
            center_label = c.get("_center_label", "")
            if center_label and center_label != "current":
                entry["near_area"] = center_label
            ba = c.get("business_area", "")
            if ba:
                entry["business_area"] = ba
            # Web 映射候选附带口碑信号
            if c.get("source") == "tavily_mapped" and c.get("web_score"):
                entry["web_reputation"] = round(float(c.get("web_score", 0)), 2)
                if c.get("web_content"):
                    entry["web_snippet"] = c.get("web_content", "")[:80]
            candidates_summary.append(entry)

        # 用户画像摘要
        profile_summary = {}
        if user_profile and isinstance(user_profile, dict):
            profile_summary["name"] = user_profile.get("name", "")
            lifestyle = user_profile.get("lifestyle_preferences", {})
            if isinstance(lifestyle, dict):
                profile_summary["dining_preferences"] = lifestyle.get("dining", [])[:5]
                profile_summary["cuisine_types"] = lifestyle.get("cuisine_types", [])[:3]
                profile_summary["price_range"] = lifestyle.get("price_range", "")
            travel = user_profile.get("travel_preferences", {})
            if isinstance(travel, dict):
                profile_summary["parking_preference"] = travel.get("parking_preference", "")
            if frequent_area_labels:
                profile_summary["frequent_areas"] = frequent_area_labels[:6]

        # 场景描述
        companion_desc = {
            "alone": "一个人，适合安静/便捷",
            "couple": "情侣/约会，适合浪漫/有氛围",
            "friends": "朋友/闺蜜聚会，适合热闹/好聊天/有氛围/出片",
            "family_kids": "带小孩，适合亲子友好/有儿童设施",
            "family_elders": "带老人，适合环境舒适/口味清淡/方便到达/少走路",
            "business": "商务应酬，适合高端/私密/有包间",
        }.get(companion_type, "")
        occasion_desc = {
            "casual": "日常随意",
            "date": "约会",
            "business": "商务",
            "celebration": "庆祝",
            "family": "家庭活动",
        }.get(occasion, "")

        time_desc = {
            "early_morning": "清晨(5-7点)",
            "morning": "早高峰(7-10点)",
            "late_morning": "上午(10-11:30)",
            "lunch": "午餐时段(11:30-13:30)",
            "afternoon": "下午(13:30-17)",
            "evening_rush": "晚高峰(17-19点)",
            "dinner": "晚餐时段(19-21点)",
            "night": "夜间(21-24点)",
            "late_night": "深夜(0-5点)",
        }.get(time_bucket, "")

        system_prompt = (
            "你是一位专业的出行推荐助手，需要从候选 POI 中选出最符合用户需求的 Top N。\n\n"
            "## 评分维度（5层漏斗，按优先级排序）\n"
            "1. **空间可达层（最重要）**：距离在合理范围内（social_radius_km 内），能方便到达。距离过远直接降权。\n"
            "2. **时间适配层**：当前时段是否适合去这家店（如深夜不该推荐商场/早餐店，上午不该推荐酒吧）。\n"
            "3. **用户匹配层**：\n"
            "   - 位于用户常去区域（frequent_areas）或其附近商圈的 POI **显著加分**（这是最高优先级）\n"
            "   - 符合用户的餐饮调性偏好和菜系偏好\n"
            "   - 价格区间与用户 price_range 匹配\n"
            "4. **质量口碑层**：rating 评分高、有网络口碑提及的优先；评分高但无人知晓的小店可适当推荐。\n"
            "5. **场景意图层**：\n"
            "   - 匹配用户当前 intent（dining/drinks/coffee/shopping/leisure）\n"
            "   - 匹配同行人类型（companion_type）：带老人→环境舒适少走路；闺蜜聚会→有氛围/出片/适合聊天；商务→高端私密\n"
            "   - 天气适配：恶劣天气户外场所扣分\n\n"
            "## 关键要求\n"
            "- **必须有差异化**：推荐的 3 个 POI 必须覆盖**不同区域**（至少一个靠近用户常去区域，不要 3 个都在当前位置附近），\n"
            "  同时价位/风格也要有区分度，给用户真实的选择空间。\n"
            "- near_area 字段标识了该 POI 靠近的常去区域名，如果有这个字段且与用户 frequent_areas 匹配，优先考虑。\n"
            "- 按推荐顺序排列：最推荐的排第一位。\n\n"
            "输出 JSON 格式：{\"recommended_indexes\": [索引列表], \"reasoning\": \"一句话推荐理由，体现各维度的考量\"}\n"
        )

        context_info = {
            "intent": intent,
            "keyword": keyword,
            "current_time": time_desc,
            "companion": companion_desc,
            "occasion": occasion_desc,
            "user_profile": profile_summary,
            "weather": weather,
            "center_position": center,
            "social_radius_km": (user_profile or {}).get("travel_preferences", {}).get("social_radius_km", 15) if isinstance(user_profile, dict) else 15,
            "top_n": limit,
            "candidates": candidates_summary,
        }
        # 清理空值字段
        context_info = {k: v for k, v in context_info.items() if v}
        user_message = json.dumps(context_info, ensure_ascii=False)

        messages = [{"role": "user", "content": user_message}]

        try:
            resp = await claude.chat(
                messages,
                system=system_prompt,
                tools=None,
            )
        except ClaudeError:
            raise

        # 从响应中提取 JSON
        text = ""
        for block in resp.content:
            if block.type == "text":
                text += block.text

        # 尝试解析 JSON（Claude 返回格式可能多样，做容错处理）
        indexes = list(range(limit))
        reasoning = "综合多维度评分，为您精选推荐。"

        try:
            # 找到 JSON 对象的起止
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                raise ValueError("no JSON found")

            json_str = text[start:end]
            result = json.loads(json_str)

            # 提取推荐索引（支持多种字段名和格式）
            raw_indexes = (
                result.get("recommended_indexes")
                or result.get("recommended")
                or result.get("top_picks")
                or []
            )
            if isinstance(raw_indexes, list):
                parsed = []
                for item in raw_indexes:
                    if isinstance(item, int):
                        parsed.append(item)
                    elif isinstance(item, dict):
                        idx = item.get("index") or item.get("idx") or item.get("rank")
                        if idx is not None:
                            parsed.append(int(idx))
                if parsed:
                    indexes = parsed

            # 提取推荐理由（可能是字符串或 dict）
            raw_reasoning = result.get("reasoning") or result.get("summary") or result.get("explanation", "")
            if isinstance(raw_reasoning, str) and raw_reasoning:
                reasoning = raw_reasoning
            elif isinstance(raw_reasoning, dict):
                # 把 dict 转为一句话
                parts = []
                for v in raw_reasoning.values():
                    if isinstance(v, str) and v:
                        parts.append(v)
                if parts:
                    reasoning = "；".join(parts[:3])

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("failed to parse AI recommendation JSON: %s", e)
            # 降级：按距离排序
            indexes = list(range(limit))
            reasoning = "AI 评分失败，按距离由近到远推荐。"

        # 确保索引有效
        valid_indexes = []
        for i in indexes:
            try:
                idx = int(i)
                if 0 <= idx < len(candidates):
                    valid_indexes.append(idx)
            except (ValueError, TypeError):
                continue
        if not valid_indexes:
            valid_indexes = list(range(limit))

        # 去重（同一索引可能出现多次）
        seen_idx = set()
        unique_indexes = []
        for idx in valid_indexes:
            if idx not in seen_idx:
                seen_idx.add(idx)
                unique_indexes.append(idx)

        recommended = [candidates[i] for i in unique_indexes[:limit]]

        # 如果推荐数量不足，用距离补齐
        if len(recommended) < limit:
            used_ids = {r["id"] for r in recommended}
            for c in candidates:
                if c["id"] not in used_ids:
                    recommended.append(c)
                    if len(recommended) >= limit:
                        break

        return recommended, reasoning


def _intent_label(intent: str) -> str:
    mapping = {
        "dining": "餐厅",
        "drinks": "酒吧",
        "coffee": "咖啡厅",
        "shopping": "商场",
        "leisure": "景点",
        "parking": "停车场",
        "custom": "地点",
    }
    return mapping.get(intent, "地点")


# 全局实例
_local_poi: Optional[LocalPOISkill] = None


def get_local_poi() -> LocalPOISkill:
    global _local_poi
    if _local_poi is None:
        _local_poi = LocalPOISkill()
    return _local_poi
