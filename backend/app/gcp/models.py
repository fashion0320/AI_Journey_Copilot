"""Global Context Panel —— 8 大模块数据模型定义。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ==================== 枚举 ====================

class JourneyStatus(str, Enum):
    IDLE = "idle"
    UNDERSTANDING = "understanding"
    CLARIFYING = "clarifying"
    DESTINATION_CONFIRM = "destination_confirm"
    RECOMMENDING = "recommending"
    PLANNING = "planning"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REPLANNING = "replanning"
    ARRIVING = "arriving"
    COMPLETED = "completed"
    ENDED = "ended"


class TrafficStatus(str, Enum):
    SMOOTH = "smooth"          # 畅通
    SLOW = "slow"              # 缓行
    CONGESTED = "congested"    # 拥堵
    SEVERE = "severe"          # 严重拥堵


class TimeBucket(str, Enum):
    EARLY_MORNING = "early_morning"  # 5-7
    MORNING = "morning"              # 7-10 早高峰
    LATE_MORNING = "late_morning"    # 10-11:30
    LUNCH = "lunch"                  # 11:30-13:30
    AFTERNOON = "afternoon"          # 13:30-17
    EVENING_RUSH = "evening_rush"    # 17-19 晚高峰
    DINNER = "dinner"                # 19-21
    NIGHT = "night"                  # 21-24
    LATE_NIGHT = "late_night"        # 0-5


class Gear(str, Enum):
    PARK = "P"
    REVERSE = "R"
    NEUTRAL = "N"
    DRIVE = "D"


class FlightStatus(str, Enum):
    SCHEDULED = "scheduled"   # 计划中
    DELAYED = "delayed"       # 延误
    BOARDING = "boarding"     # 登机中
    DEPARTED = "departed"     # 已起飞
    ARRIVED = "arrived"       # 已到达
    CANCELLED = "cancelled"   # 取消


class IntentType(str, Enum):
    UNKNOWN = "unknown"
    GOAL_DRIVEN = "goal_driven"      # 目标驱动型
    INTENT_DRIVEN = "intent_driven"  # 意图驱动型


# ==================== 位置 ====================

class Position(BaseModel):
    lat: float = 0.0
    lon: float = 0.0
    heading: Optional[float] = None  # 朝向角度
    address: Optional[str] = None    # 可读地址（可选）


# ==================== 1. Vehicle Signals ====================

class VehicleSignals(BaseModel):
    position: Position = Field(default_factory=Position)
    position_source: str = "external"  # external（GCP面板/外部事件）/ demo_sim（demo模拟自生成）
    gear: Gear = Gear.PARK
    speed_kmh: float = 0.0
    fuel_level_pct: float = 80.0  # 电量/油量百分比
    ignition_on: bool = False
    mileage_km: float = 0.0


# ==================== 2. In-cabin Perception ====================

class CabinOccupant(BaseModel):
    id: str = ""
    name: str = ""
    seat: str = ""  # driver / front_passenger / rear_left / rear_right
    age_group: str = "adult"  # child / teen / adult / senior
    gender: str = "unknown"   # male / female / unknown


class InCabinPerception(BaseModel):
    current_driver_id: str = ""
    passengers: List[CabinOccupant] = Field(default_factory=list)
    current_behavior: str = "normal"        # normal / talking / sleeping / looking_down
    dangerous_behavior: str = ""             # 空字符串表示无


# ==================== 3. Time Context ====================

class TimeContext(BaseModel):
    datetime_iso: str = ""     # ISO8601
    timestamp: float = 0.0     # Unix 时间戳
    time_bucket: TimeBucket = TimeBucket.AFTERNOON
    day_of_week: int = 1       # 1=周一, 7=周日
    is_weekend: bool = False
    is_holiday: bool = False
    holiday_name: Optional[str] = None
    season: str = "spring"     # spring / summer / autumn / winter


# ==================== 4. Weather Context ====================

class WeatherLive(BaseModel):
    province: str = ""
    city: str = ""
    adcode: str = ""
    weather: str = "晴"         # 晴 / 多云 / 阴 / 小雨 / 雷阵雨 ...
    temperature: float = 22.0   # ℃
    winddirection: str = ""     # 风向
    windpower: str = ""         # 风力等级（如 "3" 或 "3-4"）
    humidity: str = ""
    reporttime: str = ""


class WeatherForecast(BaseModel):
    date: str = ""              # YYYY-MM-DD
    week: str = ""
    dayweather: str = "晴"
    nightweather: str = "晴"
    daytemp: float = 0.0
    nighttemp: float = 0.0
    daywind: str = ""
    nightwind: str = ""
    daypower: str = ""
    nightpower: str = ""


class WeatherContext(BaseModel):
    city: str = "上海"
    adcode: str = "310000"
    live: WeatherLive = Field(default_factory=WeatherLive)
    casts: List[WeatherForecast] = Field(default_factory=list)  # 未来几天预报


# ==================== 5. Traffic Context ====================

class TrafficOnRoute(BaseModel):
    """路线相关交通状态（Skill 从路径规划 tmcs 聚合得到）。"""
    overall_status: TrafficStatus = TrafficStatus.SMOOTH
    avg_speed_kmh: float = 60.0
    worst_segment_desc: str = ""
    worst_segment_delay_min: int = 0
    total_delay_min: int = 0
    updated_at: float = 0.0


class TrafficRegion(BaseModel):
    """区域交通状态（来自交通态势查询，可选）。"""
    status: TrafficStatus = TrafficStatus.SMOOTH
    description: str = ""
    roads: List[Dict[str, Any]] = Field(default_factory=list)
    updated_at: float = 0.0


class TrafficContext(BaseModel):
    on_route: TrafficOnRoute = Field(default_factory=TrafficOnRoute)
    region: TrafficRegion = Field(default_factory=TrafficRegion)


# ==================== 6. Journey State ====================

class RouteWaypoint(BaseModel):
    name: str = ""
    location: Position = Field(default_factory=Position)
    eta: Optional[str] = None
    duration_min: int = 0
    distance_km: float = 0.0


class JourneyRoute(BaseModel):
    route_id: str = ""
    summary: str = ""
    distance_km: float = 0.0
    duration_min: int = 0
    toll_cny: float = 0.0
    waypoints: List[RouteWaypoint] = Field(default_factory=list)
    polyline_ref: str = ""   # 高德路线 ID / 编码坐标
    strategy: str = ""       # 路线策略：fastest / shortest / no_toll ...


class JourneyState(BaseModel):
    journey_id: str = ""
    status: JourneyStatus = JourneyStatus.IDLE
    intent_type: IntentType = IntentType.UNKNOWN
    user_query: str = ""
    destination_name: str = ""
    destination: Position = Field(default_factory=Position)
    route: Optional[JourneyRoute] = None
    current_leg_index: int = 0
    progress_pct: float = 0.0
    eta_arrival: Optional[str] = None     # ISO 时间
    eta_remaining_min: int = 0
    next_stop: Optional[str] = None
    started_at: Optional[str] = None
    updated_at: float = 0.0


# ==================== 7. Transit Context（航班虚拟数据） ====================

class TransitContext(BaseModel):
    flight_no: str = ""
    airline: str = ""
    departure_city: str = ""
    departure_airport: str = ""
    arrival_city: str = ""
    arrival_airport: str = ""
    terminal: str = ""            # T1 / T2 ...
    gate: str = ""
    sta: str = ""                 # scheduled time of arrival
    std: str = ""                 # scheduled time of departure
    ata: str = ""                 # actual time of arrival
    atd: str = ""                 # actual time of departure
    status: FlightStatus = FlightStatus.SCHEDULED
    delay_min: int = 0
    luggage_carousel: str = ""
    updated_at: float = 0.0


# ==================== 8. User Profile ====================

class TravelPreferences(BaseModel):
    route_preference: str = "time_first"  # time_first / no_toll / shortest / balance
    frequent_pois: List[Dict[str, Any]] = Field(default_factory=list)  # [{name, address, lat, lon, tag}]
    social_radius_km: float = 15.0
    travel_time_pattern: List[str] = Field(default_factory=list)  # ["morning_commute", "evening_commute"]
    parking_preference: str = "convenience"  # convenience / cheap / balance
    max_detour_min: int = 15
    accept_highway: bool = True


class LifestylePreferences(BaseModel):
    dining: List[str] = Field(default_factory=list)    # 调性标签
    coffee: List[str] = Field(default_factory=list)
    shopping: List[str] = Field(default_factory=list)
    leisure: List[str] = Field(default_factory=list)
    cuisine_types: List[str] = Field(default_factory=list)  # 菜系偏好
    price_range: str = "mid"  # budget / mid / premium / luxury


class UserProfile(BaseModel):
    id: str = ""
    name: str = ""
    age: int = 30
    gender: str = ""
    occupation: str = ""
    city: str = "上海"
    home_address: str = ""
    home_location: Position = Field(default_factory=Position)
    office_address: str = ""
    office_location: Position = Field(default_factory=Position)
    travel_preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    lifestyle_preferences: LifestylePreferences = Field(default_factory=LifestylePreferences)
    family_members: List[str] = Field(default_factory=list)


# ==================== 汇总 GCP 快照 ====================

class GlobalContext(BaseModel):
    """GCP 完整状态快照。"""
    vehicle: VehicleSignals = Field(default_factory=VehicleSignals)
    in_cabin: InCabinPerception = Field(default_factory=InCabinPerception)
    time: TimeContext = Field(default_factory=TimeContext)
    weather: WeatherContext = Field(default_factory=WeatherContext)
    traffic: TrafficContext = Field(default_factory=TrafficContext)
    journey: JourneyState = Field(default_factory=JourneyState)
    transit: TransitContext = Field(default_factory=TransitContext)
    user_profile: UserProfile = Field(default_factory=UserProfile)

    def slice_for_skill(self, fields: List[str]) -> Dict[str, Any]:
        """根据字段路径列表，截取 GCP 的子集。

        fields 示例: ["vehicle.position", "weather.live.weather"]
        """
        data = self.model_dump()
        result: Dict[str, Any] = {}
        for field in fields:
            parts = field.split(".")
            src = data
            dst = result
            for i, part in enumerate(parts):
                if part not in src:
                    break
                if i == len(parts) - 1:
                    dst[part] = src[part]
                else:
                    if part not in dst:
                        dst[part] = {}
                    dst = dst[part]
                    src = src[part]
        return result
