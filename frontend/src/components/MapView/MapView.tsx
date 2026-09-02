import { useEffect, useRef, useMemo } from 'react'
import { useAmap } from '@/hooks/useAmap'
import { useGcpStore } from '@/store/gcpStore'
import { useJourneyStore } from '@/store/journeyStore'
import type { EtaData, ParkingLot } from '@/types'
import './MapView.css'

export default function MapView() {
  const { context } = useGcpStore()
  // 订阅 cards 以确保卡片更新时地图 overlay 能重新渲染
  const cards = useJourneyStore((s) => s.cards)
  const journeyStatus = useJourneyStore((s) => s.journeyStatus)
  const getRoutePolyline = useJourneyStore((s) => s.getRoutePolyline)
  const getDestination = useJourneyStore((s) => s.getDestination)
  const getEta = useJourneyStore((s) => s.getEta)
  const getParkingLots = useJourneyStore((s) => s.getParkingLots)

  // 预计算派生数据（在渲染时同步计算，确保 effect 依赖项正确）
  const routePoints = useMemo(() => getRoutePolyline(), [cards, getRoutePolyline])
  const dest = useMemo(() => getDestination(), [cards, getDestination])
  const eta = useMemo(() => getEta(), [cards, getEta])
  const parkingLots = useMemo(() => getParkingLots(), [cards, getParkingLots])

  const { map, containerRef, setMarker, setPolyline, removeOverlay, fitToPoints } = useAmap(
    context?.vehicle?.position
      ? [context.vehicle.position.lon, context.vehicle.position.lat]
      : [121.4737, 31.2304],
    12,
  )

  const fitOnceRef = useRef(false)

  // ---- Vehicle position marker ----
  useEffect(() => {
    if (!map || !context?.vehicle?.position) return
    const { lat, lon } = context.vehicle.position

    const content = `<div style="
      width: 20px; height: 20px; border-radius: 50%;
      background: #6366f1; border: 3px solid #fff;
      box-shadow: 0 2px 8px rgba(99,102,241,0.6);
    "></div>`

    setMarker('vehicle', [lon, lat], { content, offset: [-10, -10], zIndex: 100 })
  }, [map, context?.vehicle?.position?.lat, context?.vehicle?.position?.lon, setMarker])

  // ---- Route polyline ----
  useEffect(() => {
    if (!map) return
    if (routePoints.length >= 2) {
      setPolyline('route', routePoints, {
        strokeColor: '#6366f1',
        strokeWeight: 6,
        strokeOpacity: 0.9,
        zIndex: 10,
      })
      // Auto-fit bounds once when route first appears
      if (!fitOnceRef.current) {
        const vehPos = context?.vehicle?.position
        const points = [...routePoints]
        if (vehPos) points.push([vehPos.lon, vehPos.lat] as [number, number])
        fitToPoints(points, [80, 80, 80, 80])
        fitOnceRef.current = true
      }
    } else {
      removeOverlay('route')
    }
  }, [map, routePoints, setPolyline, removeOverlay, fitToPoints, context?.vehicle?.position])

  // Reset fit when journey resets or new route appears during replanning
  const prevRouteLenRef = useRef(0)
  useEffect(() => {
    if (journeyStatus === 'idle' || journeyStatus === 'completed') {
      fitOnceRef.current = false
      prevRouteLenRef.current = 0
    }
    // 重规划时，如果路线变短/完全不同，重新 fit
    if (journeyStatus === 'replanning') {
      fitOnceRef.current = false
    }
  }, [journeyStatus])

  // ---- Destination marker ----
  useEffect(() => {
    if (!map) return
    if (dest) {
      const content = `<div style="
        width: 32px; height: 32px; border-radius: 50%;
        background: linear-gradient(135deg,#ef4444,#f97316);
        border: 3px solid #fff;
        box-shadow: 0 2px 12px rgba(239,68,68,0.5);
        display:flex;align-items:center;justify-content:center;
        font-size:16px;
      ">📍</div>`
      setMarker('destination', [dest.lon, dest.lat], { content, offset: [-16, -16], zIndex: 90 })
    } else {
      removeOverlay('destination')
    }
  }, [map, dest?.lat, dest?.lon, dest?.name, setMarker, removeOverlay])

  // ---- Parking markers (arriving phase) ----
  useEffect(() => {
    if (!map) return
    const isArriving = journeyStatus === 'arriving' || journeyStatus === 'in_progress'

    // Remove old parking markers first (clean up all possible slots)
    for (let i = 0; i < 10; i++) {
      removeOverlay(`parking_${i}`)
    }

    if (isArriving && parkingLots.length > 0) {
      parkingLots.slice(0, 5).forEach((lot: ParkingLot, i: number) => {
        const lon = lot.lon ?? lot.lng ?? lot.location?.lon ?? lot.location?.lng
        const lat = lot.lat ?? lot.location?.lat
        if (typeof lon === 'number' && typeof lat === 'number') {
          const isRecommended = i === 0
          const bg = isRecommended ? '#10b981' : '#64748b'
          const content = `<div style="
            width: 26px; height: 26px; border-radius: 50%;
            background: ${bg}; border: 2px solid #fff;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            display:flex;align-items:center;justify-content:center;
            font-size:13px;color:#fff;font-weight:600;
          ">P</div>`
          setMarker(`parking_${i}`, [lon, lat], { content, offset: [-13, -13], zIndex: 80 })
        }
      })
    }
  }, [map, journeyStatus, parkingLots, setMarker, removeOverlay])

  const showEtaOverlay = !!eta && (journeyStatus === 'in_progress' || journeyStatus === 'ready' || journeyStatus === 'replanning')

  return (
    <div className="map-view">
      <div ref={containerRef} className="map-container" />

      {/* Top-left overlay chips */}
      <div className="map-overlay">
        {context?.user_profile?.name && (
          <div className="user-chip">
            👤 {context.user_profile.name} · {context.user_profile.occupation}
          </div>
        )}
        {context?.journey?.status && (
          <div className="journey-chip">
            🚗 旅程状态: {context.journey.status}
          </div>
        )}
      </div>

      {/* Floating ETA overlay (top-right) */}
      {showEtaOverlay && eta && (
        <div className="map-eta-overlay">
          <EtaMini eta={eta} destination={dest?.name} />
        </div>
      )}
    </div>
  )
}

/**
 * Compact ETA display for the map overlay
 */
function EtaMini({ eta, destination }: { eta: EtaData; destination?: string }) {
  const trafficColors: Record<string, string> = {
    smooth: '#4ade80',
    slow: '#fbbf24',
    congested: '#f87171',
    severe: '#ef4444',
    unknown: '#94a3b8',
  }
  const trafficLabels: Record<string, string> = {
    smooth: '畅通',
    slow: '缓行',
    congested: '拥堵',
    severe: '严重拥堵',
    unknown: '未知',
  }
  const dotColor = trafficColors[eta.traffic_level || 'unknown'] || trafficColors.unknown
  const tLabel = trafficLabels[eta.traffic_level || 'unknown'] || '未知'

  return (
    <div className="eta-mini">
      <div className="eta-mini-row">
        <span className="eta-mini-number">{eta.remaining_min ?? '--'}</span>
        <span className="eta-mini-unit">分钟</span>
      </div>
      {eta.eta_arrival_time && (
        <div className="eta-mini-arrival">预计 {eta.eta_arrival_time} 到达</div>
      )}
      {destination && (
        <div className="eta-mini-dest">→ {destination}</div>
      )}
      <div className="eta-mini-traffic">
        <span className="traffic-dot" style={{ background: dotColor }} />
        {tLabel}
      </div>
    </div>
  )
}
