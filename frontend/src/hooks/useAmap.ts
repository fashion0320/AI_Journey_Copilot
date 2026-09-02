import { useEffect, useState, useRef, useCallback } from 'react'

declare global {
  interface Window {
    AMap?: any
    _AMapSecurityConfig?: any
    __amap_init_callback?: () => void
  }
}

const JS_API_URL = 'https://webapi.amap.com/maps?v=2.0&key='

interface MarkerOptions {
  icon?: string
  content?: string
  offset?: [number, number]
  zIndex?: number
}

/**
 * 高德地图 JS API 异步加载 Hook。
 * 用法：const { map, AMap, containerRef, setMarker, setPolyline, removeOverlay } = useAmap(center, zoom)
 */
export function useAmap(center: [number, number] = [121.4737, 31.2304], zoom = 13) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [AMap, setAMap] = useState<any>(null)
  const [map, setMap] = useState<any>(null)
  const initedRef = useRef(false)
  const overlaysRef = useRef<Map<string, any>>(new Map())
  const centerRef = useRef(center)
  const zoomRef = useRef(zoom)
  centerRef.current = center
  zoomRef.current = zoom

  useEffect(() => {
    const key = import.meta.env.VITE_AMAP_JS_KEY
    const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE

    const initMap = () => {
      if (!containerRef.current || !window.AMap || initedRef.current) return
      initedRef.current = true
      const m = new window.AMap.Map(containerRef.current, {
        center: centerRef.current,
        zoom: zoomRef.current,
        mapStyle: 'amap://styles/darkblue',
        features: ['bg', 'road', 'building', 'point'],
      })
      setAMap(window.AMap)
      setMap(m)
    }

    if (window.AMap) {
      initMap()
      return
    }

    if (securityCode) {
      window._AMapSecurityConfig = { securityJsCode: securityCode }
    }

    if (key) {
      const script = document.createElement('script')
      script.src = `${JS_API_URL}${key}`
      script.async = true
      script.onload = initMap
      document.head.appendChild(script)
    } else {
      console.warn('[AMap] VITE_AMAP_JS_KEY not set, map will not load')
    }

    return () => {
      // 不销毁全局 AMap，保持单例
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 当 center 变化时平移地图（vehicle 位置异步加载后定位）
  const lastCenterRef = useRef<[number, number] | null>(null)
  useEffect(() => {
    if (!map) return
    const [lng, lat] = center
    // 避免首次初始化时重复 setCenter
    if (lastCenterRef.current
        && Math.abs(lastCenterRef.current[0] - lng) < 0.0001
        && Math.abs(lastCenterRef.current[1] - lat) < 0.0001) return
    lastCenterRef.current = center
    // 只在没有路线显示时（idle/understanding/clarifying阶段）才自动平移到车辆位置
    const overlays = overlaysRef.current
    if (!overlays.has('route') && !overlays.has('destination')) {
      map.setCenter(center)
    }
  }, [map, center[0], center[1]])

  // —— 覆盖物管理工具方法 ——

  const removeOverlay = useCallback((id: string) => {
    if (!map) return
    const overlay = overlaysRef.current.get(id)
    if (overlay) {
      map.remove(overlay)
      overlaysRef.current.delete(id)
    }
  }, [map])

  const setMarker = useCallback(
    (id: string, position: [number, number], options: MarkerOptions = {}) => {
      if (!map || !AMap) return
      const existing = overlaysRef.current.get(id)
      if (existing) {
        existing.setPosition(position)
        // 同时更新 content（如果提供了新的）
        if (options.content) {
          existing.setContent(options.content)
        }
        if (options.zIndex) {
          existing.setzIndex(options.zIndex)
        }
        return
      }

      const markerOpts: any = {
        position,
        zIndex: options.zIndex || 50,
      }

      if (options.content) {
        markerOpts.content = options.content
        markerOpts.offset = new AMap.Pixel(
          options.offset?.[0] ?? -10,
          options.offset?.[1] ?? -10,
        )
      }

      const marker = new AMap.Marker(markerOpts)
      marker.setMap(map)
      overlaysRef.current.set(id, marker)
    },
    [map, AMap],
  )

  const setPolyline = useCallback(
    (id: string, path: Array<[number, number]>, options: any = {}) => {
      if (!map || !AMap) return
      // 已有则更新
      const existing = overlaysRef.current.get(id)
      if (existing) {
        existing.setPath(path)
        return
      }

      const polyline = new AMap.Polyline({
        path,
        strokeColor: options.strokeColor || '#6366f1',
        strokeWeight: options.strokeWeight || 6,
        strokeOpacity: options.strokeOpacity || 0.9,
        lineJoin: 'round',
        lineCap: 'round',
        zIndex: options.zIndex || 10,
      })
      polyline.setMap(map)
      overlaysRef.current.set(id, polyline)
    },
    [map, AMap],
  )

  const setCircle = useCallback(
    (id: string, center: [number, number], radius: number, options: any = {}) => {
      if (!map || !AMap) return
      const existing = overlaysRef.current.get(id)
      if (existing) {
        existing.setCenter(center)
        existing.setRadius(radius)
        return
      }
      const circle = new AMap.Circle({
        center,
        radius,
        strokeColor: options.strokeColor || '#6366f1',
        strokeWeight: 2,
        strokeOpacity: 0.6,
        fillColor: options.fillColor || '#6366f1',
        fillOpacity: options.fillOpacity || 0.12,
        zIndex: options.zIndex || 9,
      })
      circle.setMap(map)
      overlaysRef.current.set(id, circle)
    },
    [map, AMap],
  )

  const fitToPoints = useCallback(
    (points: Array<[number, number]>, padding: [number, number, number, number] = [60, 60, 60, 60]) => {
      if (!map || !AMap || points.length === 0) return
      if (points.length === 1) {
        map.setZoomAndCenter(14, points[0])
        return
      }
      const bounds = new AMap.Bounds(
        new AMap.LngLat(
          Math.min(...points.map((p) => p[0])),
          Math.min(...points.map((p) => p[1])),
        ),
        new AMap.LngLat(
          Math.max(...points.map((p) => p[0])),
          Math.max(...points.map((p) => p[1])),
        ),
      )
      map.setBounds(bounds, false, padding)
    },
    [map, AMap],
  )

  const clearAll = useCallback(() => {
    if (!map) return
    overlaysRef.current.forEach((overlay) => {
      map.remove(overlay)
    })
    overlaysRef.current.clear()
  }, [map])

  return {
    AMap,
    map,
    containerRef,
    setMarker,
    setPolyline,
    setCircle,
    removeOverlay,
    fitToPoints,
    clearAll,
  }
}
