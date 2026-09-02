"""高德地图 polyline 编解码工具。

高德地图 Web 服务 API（v5 驾车路线规划）返回的 polyline 字段是经过编码的字符串，
采用类似 Google polyline 的算法，但坐标顺序是 (经度, 纬度)。

编码规则（增量编码 + 可变长度整数）：
1. 将坐标乘以 1e5 并四舍五入为整数
2. 计算与前一个坐标的差值（delta）
3. 对 delta 进行有符号整数编码：
   - 正数：先左移 1 位
   - 负数：先左移 1 位后取反
4. 使用 base64-like 可变长度编码：每 5 位编码到一个字符，
   字符集为 ASCII 63-126（即 '?' 到 '~'），用最高位表示是否还有后续字节

为了前后端通用，我们在后端将其解码为 "lon,lat;lon,lat;..." 的纯文本格式。
"""

from __future__ import annotations

from typing import List, Tuple


def decode_amap_polyline(encoded: str) -> List[Tuple[float, float]]:
    """解码高德地图 polyline 字符串。

    Args:
        encoded: 编码后的 polyline 字符串

    Returns:
        [(lon, lat), ...] 坐标点列表
    """
    if not encoded:
        return []

    points: List[Tuple[float, float]] = []
    lon = 0.0
    lat = 0.0
    i = 0
    n = len(encoded)

    while i < n:
        # 解码经度增量
        lon_delta, i = _decode_varint(encoded, i)
        lon += lon_delta / 100000.0

        if i >= n:
            break

        # 解码纬度增量
        lat_delta, i = _decode_varint(encoded, i)
        lat += lat_delta / 100000.0

        points.append((round(lon, 6), round(lat, 6)))

    return points


def _decode_varint(s: str, start: int) -> Tuple[int, int]:
    """解码一个可变长度有符号整数。

    返回 (value, next_index)。
    """
    result = 0
    shift = 0
    i = start
    n = len(s)

    while i < n:
        c = ord(s[i]) - 63  # 高德使用 ASCII 63 ('?') 作为起始
        i += 1

        # 取低 5 位
        result |= (c & 0x1F) << shift
        shift += 5

        # 如果最高位是 0，表示结束
        if (c & 0x20) == 0:
            break

    # 有符号整数解码：最后一位是符号位
    if result & 1:
        result = ~(result >> 1)
    else:
        result = result >> 1

    return result, i


def encode_amap_polyline(points: List[Tuple[float, float]]) -> str:
    """将坐标点列表编码为高德 polyline 格式。

    Args:
        points: [(lon, lat), ...] 坐标点列表

    Returns:
        编码后的 polyline 字符串
    """
    if not points:
        return ""

    parts: List[str] = []
    prev_lon = 0.0
    prev_lat = 0.0

    for lon, lat in points:
        lon_int = round(lon * 100000)
        lat_int = round(lat * 100000)

        lon_delta = lon_int - round(prev_lon * 100000)
        lat_delta = lat_int - round(prev_lat * 100000)

        parts.append(_encode_signed_int(lon_delta))
        parts.append(_encode_signed_int(lat_delta))

        prev_lon = lon
        prev_lat = lat

    return "".join(parts)


def _encode_signed_int(value: int) -> str:
    """编码一个有符号整数。"""
    # 有符号整数编码
    if value < 0:
        value = (~(-value)) << 1 | 1
    else:
        value = value << 1

    # 可变长度编码
    result = ""
    while value >= 0x20:
        result += chr((value & 0x1F) | 0x20 + 63)
        value >>= 5
    result += chr(value + 63)

    return result


def polyline_to_plain(encoded: str) -> str:
    """将编码的 polyline 转换为 "lon,lat;lon,lat;..." 纯文本格式。

    这是前后端约定的通用格式，前端可以直接解析。
    """
    points = decode_amap_polyline(encoded)
    return ";".join(f"{lon},{lat}" for lon, lat in points)


def plain_to_polyline(plain: str) -> List[Tuple[float, float]]:
    """将 "lon,lat;lon,lat;..." 纯文本格式解析为坐标列表。"""
    if not plain:
        return []
    points: List[Tuple[float, float]] = []
    for seg in plain.split(";"):
        if not seg:
            continue
        parts = seg.split(",")
        if len(parts) >= 2:
            try:
                lon = float(parts[0])
                lat = float(parts[1])
                points.append((lon, lat))
            except (ValueError, IndexError):
                continue
    return points
