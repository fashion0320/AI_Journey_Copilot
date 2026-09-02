"""预设模拟数据：Amy / Claire 用户画像 + 航班 + 场景初始状态。"""

from __future__ import annotations

from datetime import datetime, timedelta
import time
from typing import Dict

from .models import (
    CabinOccupant,
    FlightStatus,
    Gear,
    InCabinPerception,
    LifestylePreferences,
    Position,
    TimeBucket,
    TimeContext,
    TransitContext,
    TravelPreferences,
    VehicleSignals,
    WeatherContext,
    WeatherForecast,
    WeatherLive,
    UserProfile,
)


# ==================== 用户画像 ====================

def build_amy_profile() -> UserProfile:
    """Amy: 37岁，跨国公司高管，商务效率优先。"""
    return UserProfile(
        id="user_amy",
        name="Amy",
        age=37,
        gender="female",
        occupation="外企高管 / 咨询合伙人",
        city="上海",
        home_address="上海市浦东新区前滩",
        home_location=Position(lat=31.1812, lon=121.5078),
        office_address="上海市浦东新区陆家嘴国金中心",
        office_location=Position(lat=31.2359, lon=121.4996),
        travel_preferences=TravelPreferences(
            route_preference="time_first",
            frequent_pois=[
                {"name": "家", "address": "前滩", "lat": 31.1812, "lon": 121.5078, "tag": "home"},
                {"name": "办公室", "address": "陆家嘴国金中心", "lat": 31.2359, "lon": 121.4996, "tag": "office"},
                {"name": "虹桥机场T2", "address": "虹桥国际机场T2", "lat": 31.1964, "lon": 121.3363, "tag": "airport"},
                {"name": "浦东机场T2", "address": "浦东国际机场T2", "lat": 31.1434, "lon": 121.8058, "tag": "airport"},
                {"name": "静安香格里拉", "address": "静安寺", "lat": 31.2234, "lon": 121.4476, "tag": "hotel"},
                {"name": "外滩华尔道夫", "address": "外滩", "lat": 31.2389, "lon": 121.4900, "tag": "hotel"},
            ],
            social_radius_km=30.0,
            travel_time_pattern=["morning_commute", "business_lunch", "evening_dinner", "airport_pickup"],
            parking_preference="convenience",
            max_detour_min=10,
            accept_highway=True,
        ),
        lifestyle_preferences=LifestylePreferences(
            dining=["商务西餐", "日料omakase", "精品酒店中餐", "私密", "包间"],
            coffee=["Manner", "Peet's", "精品独立店"],
            shopping=["品牌店", "快闪店", "国金", "久光", "兴业太古汇"],
            leisure=["健身私教", "近郊亲子", "朱家角", "佘山"],
            cuisine_types=["日料", "粤菜", "西餐"],
            price_range="premium",
        ),
        family_members=["丈夫", "儿子6岁"],
    )


def build_claire_profile() -> UserProfile:
    """Claire: 29岁，品牌市场经理，氛围感生活。"""
    return UserProfile(
        id="user_claire",
        name="Claire",
        age=29,
        gender="female",
        occupation="品牌市场经理",
        city="上海",
        home_address="上海市静安区南京西路",
        home_location=Position(lat=31.2286, lon=121.4483),
        office_address="上海市静安区静安嘉里中心",
        office_location=Position(lat=31.2249, lon=121.4520),
        travel_preferences=TravelPreferences(
            route_preference="balance",
            frequent_pois=[
                {"name": "家", "address": "静安寺", "lat": 31.2286, "lon": 121.4483, "tag": "home"},
                {"name": "办公室", "address": "静安嘉里中心", "lat": 31.2249, "lon": 121.4520, "tag": "office"},
                {"name": "安福路", "address": "安福路", "lat": 31.2166, "lon": 121.4383, "tag": "leisure"},
                {"name": "武康路", "address": "武康路", "lat": 31.2108, "lon": 121.4349, "tag": "leisure"},
                {"name": "前滩太古里", "address": "前滩太古里", "lat": 31.1799, "lon": 121.5110, "tag": "mall"},
                {"name": "新天地", "address": "新天地", "lat": 31.2222, "lon": 121.4692, "tag": "leisure"},
            ],
            social_radius_km=10.0,
            travel_time_pattern=["late_commute", "evening_social", "weekend_brunch"],
            parking_preference="convenience",
            max_detour_min=20,
            accept_highway=False,
        ),
        lifestyle_preferences=LifestylePreferences(
            dining=["bistro", "brunch", "精品咖啡", "独立小酒馆", "高颜值甜品", "氛围感", "安静", "出片", "适合聊天"],
            coffee=["% Arabica", "Blue Bottle", "独立小馆", "有故事的咖啡"],
            shopping=["买手店", "独立设计师", "香水", "护肤", "家居选物"],
            leisure=["citywalk", "近郊民宿", "瑜伽", "普拉提", "看展", "live house"],
            cuisine_types=["法餐", "意餐", "融合菜", "甜品"],
            price_range="mid",
        ),
        family_members=[],
    )


USER_PROFILES: Dict[str, UserProfile] = {
    "amy": build_amy_profile(),
    "claire": build_claire_profile(),
}


# ==================== 时间上下文工具 ====================

def build_time_context(dt: datetime) -> TimeContext:
    """根据 datetime 构造时间上下文。"""
    hour = dt.hour
    minute = dt.minute
    minutes_of_day = hour * 60 + minute

    if 5 * 60 <= minutes_of_day < 7 * 60:
        bucket = TimeBucket.EARLY_MORNING
    elif 7 * 60 <= minutes_of_day < 10 * 60:
        bucket = TimeBucket.MORNING
    elif 10 * 60 <= minutes_of_day < 11 * 60 + 30:
        bucket = TimeBucket.LATE_MORNING
    elif 11 * 60 + 30 <= minutes_of_day < 13 * 60 + 30:
        bucket = TimeBucket.LUNCH
    elif 13 * 60 + 30 <= minutes_of_day < 17 * 60:
        bucket = TimeBucket.AFTERNOON
    elif 17 * 60 <= minutes_of_day < 19 * 60:
        bucket = TimeBucket.EVENING_RUSH
    elif 19 * 60 <= minutes_of_day < 21 * 60:
        bucket = TimeBucket.DINNER
    elif 21 * 60 <= minutes_of_day < 24 * 60:
        bucket = TimeBucket.NIGHT
    else:
        bucket = TimeBucket.LATE_NIGHT

    weekday = dt.isoweekday()  # 1=周一
    is_weekend = weekday >= 6

    month = dt.month
    if 3 <= month <= 5:
        season = "spring"
    elif 6 <= month <= 8:
        season = "summer"
    elif 9 <= month <= 11:
        season = "autumn"
    else:
        season = "winter"

    return TimeContext(
        datetime_iso=dt.isoformat(),
        timestamp=dt.timestamp(),
        time_bucket=bucket,
        day_of_week=weekday,
        is_weekend=is_weekend,
        is_holiday=False,
        holiday_name=None,
        season=season,
    )


# ==================== 场景预设 ====================

def preset_amy_airport_pickup() -> Dict[str, any]:
    """Amy 接机场景初始状态。
    时间: 周五 14:15
    位置: 陆家嘴办公室
    航班: MU5301 深圳→虹桥T2，延误 30 分钟
    """
    now = datetime.now().replace(hour=14, minute=15, second=0, microsecond=0)
    # 调整到最近的周五
    while now.isoweekday() != 5:
        now += timedelta(days=1)

    std = now.replace(hour=14, minute=0, second=0)  # 计划起飞
    sta = now.replace(hour=15, minute=30, second=0)  # 计划到达
    ata = now.replace(hour=16, minute=0, second=0)   # 预计到达（延误30min）

    return {
        "user_profile": "amy",
        "vehicle": VehicleSignals(
            position=Position(lat=31.2359, lon=121.4996, address="陆家嘴国金中心"),
            gear=Gear.PARK,
            speed_kmh=0.0,
            fuel_level_pct=85.0,
            ignition_on=False,
        ),
        "in_cabin": InCabinPerception(
            current_driver_id="user_amy",
            passengers=[CabinOccupant(id="user_amy", name="Amy", seat="driver", age_group="adult", gender="female")],
            current_behavior="normal",
            dangerous_behavior="",
        ),
        "time": build_time_context(now),
        "weather": WeatherContext(
            city="上海",
            adcode="310000",
            live=WeatherLive(
                province="上海", city="上海", adcode="310000",
                weather="多云", temperature=24.0,
                winddirection="东南", windpower="3", humidity="65",
                reporttime=now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            casts=[
                WeatherForecast(date=now.strftime("%Y-%m-%d"), week="周五", dayweather="多云", nightweather="晴",
                                daytemp=25.0, nighttemp=18.0, daywind="东南", nightwind="东", daypower="3", nightpower="2"),
                WeatherForecast(date=(now + timedelta(days=1)).strftime("%Y-%m-%d"), week="周六", dayweather="晴", nightweather="晴",
                                daytemp=27.0, nighttemp=19.0, daywind="东", nightwind="东", daypower="3", nightpower="2"),
            ],
        ),
        "transit": TransitContext(
            flight_no="MU5301",
            airline="中国东方航空",
            departure_city="深圳",
            departure_airport="SZX",
            arrival_city="上海",
            arrival_airport="SHA",
            terminal="T2",
            gate="",
            sta=sta.isoformat(),
            std=std.isoformat(),
            ata=ata.isoformat(),
            atd="",
            status=FlightStatus.DELAYED,
            delay_min=30,
            luggage_carousel="",
            updated_at=time.time(),
        ),
        "description": "Amy 虹桥接机场景 —— 周五14:15，办公室出发，航班延误30分钟",
    }


def preset_claire_girls_night() -> Dict[str, any]:
    """Claire 闺蜜聚会场景初始状态。
    时间: 工作日 18:15（晚高峰）
    位置: 静安嘉里中心（办公室）
    """
    now = datetime.now().replace(hour=18, minute=15, second=0, microsecond=0)
    # 调整到最近的工作日
    while now.isoweekday() > 5:
        now += timedelta(days=1)

    return {
        "user_profile": "claire",
        "vehicle": VehicleSignals(
            position=Position(lat=31.2249, lon=121.4520, address="静安嘉里中心"),
            gear=Gear.PARK,
            speed_kmh=0.0,
            fuel_level_pct=70.0,
            ignition_on=False,
        ),
        "in_cabin": InCabinPerception(
            current_driver_id="user_claire",
            passengers=[CabinOccupant(id="user_claire", name="Claire", seat="driver", age_group="adult", gender="female")],
            current_behavior="normal",
            dangerous_behavior="",
        ),
        "time": build_time_context(now),
        "weather": WeatherContext(
            city="上海",
            adcode="310000",
            live=WeatherLive(
                province="上海", city="上海", adcode="310000",
                weather="晴", temperature=22.0,
                winddirection="西", windpower="2", humidity="55",
                reporttime=now.strftime("%Y-%m-%d %H:%M:%S"),
            ),
            casts=[
                WeatherForecast(date=now.strftime("%Y-%m-%d"), week="周三", dayweather="晴", nightweather="晴",
                                daytemp=23.0, nighttemp=17.0, daywind="西", nightwind="西", daypower="2", nightpower="2"),
            ],
        ),
        "transit": TransitContext(),  # 无航班
        "description": "Claire 闺蜜聚会场景 —— 工作日18:15，静安下班，晚高峰",
    }


PRESETS: Dict[str, Dict[str, any]] = {
    "amy_airport_pickup": preset_amy_airport_pickup(),
    "claire_girls_night": preset_claire_girls_night(),
}
