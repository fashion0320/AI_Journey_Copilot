import { create } from 'zustand'
import type {
  CardItem,
  CardType,
  JourneyStatus,
  ProposalData,
  SkillStatusData,
  JourneyReadyData,
  EtaData,
  ParkingData,
  ArrivingData,
  ClarifyData,
  ErrorData,
  PoICandidate,
  ParkingLot,
} from '@/types'
import { chatWs } from '@/services/websocket'

// 生成唯一卡片 ID
let cardIdCounter = 0
const genCardId = () => `card_${Date.now()}_${++cardIdCounter}`

interface JourneyState {
  // 卡片列表（按时间顺序）
  cards: CardItem[]

  // 旅程状态
  journeyStatus: JourneyStatus

  // 当前选中的方案
  selectedPlanId: string | null

  // —— Actions ——

  // 添加卡片（自动去重：相同 type + request_id 则更新）
  addCard: (type: CardType, data: any, request_id: string) => CardItem

  // 按 ID 更新卡片
  updateCard: (id: string, updates: Partial<CardItem>) => void

  // 按 type + request_id 找卡片
  findCard: (type: CardType, request_id: string) => CardItem | undefined

  // 更新旅程状态
  setJourneyStatus: (status: JourneyStatus) => void

  // —— 交互方法（调用 WebSocket）——

  // 选择方案
  selectPlan: (planId: string) => void

  // 确认出发
  confirmDeparture: () => void

  // 回复澄清问题
  sendClarifyReply: (answer: string) => void

  // 选择候选 POI
  selectCandidate: (candidate: PoICandidate) => void

  // 重置
  reset: () => void

  // 清除结果类卡片（多轮修改或新query时调用），保留proposals/clarify等交互类卡片
  clearResultCards: () => void

  // —— Derived selectors (map-friendly) ——

  // 路线 polyline 点数组 [[lon, lat], ...]
  getRoutePolyline: () => Array<[number, number]>

  // 目的地坐标 [lon, lat]
  getDestination: () => { name: string; lon: number; lat: number } | null

  // 最新 ETA 数据
  getEta: () => EtaData | null

  // 停车场列表
  getParkingLots: () => ParkingLot[]

  // 推荐停车场
  getRecommendedParking: () => ParkingLot | null
}

export const useJourneyStore = create<JourneyState>((set, get) => ({
  cards: [],
  journeyStatus: 'idle',
  selectedPlanId: null,

  addCard: (type, data, request_id) => {
    const state = get()
    // 去重：同 type + request_id 视为同一张卡，更新数据
    const existing = state.cards.find(
      (c) => c.type === type && c.request_id === request_id,
    )

    if (existing) {
      set({
        cards: state.cards.map((c) =>
          c.id === existing.id
            ? { ...c, data, timestamp: Date.now() }
            : c,
        ),
      })
      return { ...existing, data, timestamp: Date.now() }
    }

    // 对于 proposals、clarify、eta 类型，每次新推荐/追问/更新替换旧的同类型卡片
    let filteredCards = state.cards
    if (type === 'proposals' || type === 'clarify' || type === 'eta' || type === 'thought_process') {
      filteredCards = state.cards.filter((c) => c.type !== type)
    }

    const newCard: CardItem = {
      id: genCardId(),
      type,
      data,
      timestamp: Date.now(),
      request_id,
    }

    // 当新的 proposals 卡片到达时，重置 selectedPlanId（避免旧选择阻止新卡片点击）
    const extraState: Partial<JourneyState> = {}
    if (type === 'proposals') {
      extraState.selectedPlanId = null
    }

    set({ cards: [...filteredCards, newCard], ...extraState })
    return newCard
  },

  updateCard: (id, updates) => {
    set((state) => ({
      cards: state.cards.map((c) =>
        c.id === id ? { ...c, ...updates, data: { ...c.data, ...(updates.data || {}) } } : c,
      ),
    }))
  },

  findCard: (type, request_id) => {
    return get().cards.find(
      (c) => c.type === type && c.request_id === request_id,
    )
  },

  setJourneyStatus: (status) => set({ journeyStatus: status }),

  selectPlan: (planId) => {
    set({ selectedPlanId: planId })
    chatWs.send('journey_action', { action: 'select_plan', plan_id: planId })
  },

  confirmDeparture: () => {
    chatWs.send('journey_action', { action: 'confirm_departure' })
  },

  sendClarifyReply: (answer) => {
    chatWs.send('clarify_reply', { answer })
  },

  selectCandidate: (candidate) => {
    // 选择候选 POI — 使用 journey_action 直接确认目的地
    // 规范化 location（可能是 string "lon,lat" 或 {lat, lon}）
    let normLoc = candidate.location
    if (typeof candidate.location === 'string' && candidate.location.includes(',')) {
      const [lonStr, latStr] = candidate.location.split(',')
      normLoc = { lon: parseFloat(lonStr), lat: parseFloat(latStr) }
    }
    chatWs.send('journey_action', {
      action: 'select_candidate',
      candidate: {
        id: candidate.id,
        name: candidate.name,
        address: candidate.address,
        location: normLoc,
      },
    })
  },

  reset: () =>
    set({
      cards: [],
      journeyStatus: 'idle',
      selectedPlanId: null,
    }),

  // 清除结果类卡片，保留 proposals/clarify 等交互类卡片（用于多轮修改或新query中断时）
  clearResultCards: () =>
    set((state) => ({
      cards: state.cards.filter((c) =>
        !['journey_ready', 'eta', 'parking', 'route', 'arriving', 'skill', 'thought_process'].includes(c.type),
      ),
      selectedPlanId: null,
    })),

  // —— Derived selectors ——

  getRoutePolyline: () => {
    const state = get()
    // Look for latest journey_ready or route card with polyline
    const cards = [...state.cards].reverse()
    for (const c of cards) {
      if (c.type === 'journey_ready' && c.data?.route?.polyline) {
        return decodeAmapPolyline(c.data.route.polyline)
      }
      if (c.type === 'route' && c.data?.polyline) {
        return decodeAmapPolyline(c.data.polyline)
      }
    }
    return []
  },

  getDestination: () => {
    const state = get()
    const cards = [...state.cards].reverse()
    for (const c of cards) {
      if (c.type === 'journey_ready' && c.data?.destination) {
        const d = c.data.destination
        if (typeof d.lat === 'number' && typeof d.lon === 'number'
            && d.lat !== 0 && d.lon !== 0) {
          return { name: d.name || '目的地', lon: d.lon, lat: d.lat }
        }
      }
      if (c.type === 'arriving' && c.data?.parking_lots?.length) {
        const lot = c.data.parking_lots[0]
        const lat = lot.lat ?? lot.location?.lat ?? lot.position?.lat
        const lon = lot.lon ?? lot.lng ?? lot.location?.lon ?? lot.location?.lng ?? lot.position?.lon
        if (typeof lat === 'number' && typeof lon === 'number' && lat !== 0 && lon !== 0) {
          return { name: c.data.destination || '目的地', lon, lat }
        }
      }
    }
    return null
  },

  getEta: () => {
    const state = get()
    const cards = [...state.cards].reverse()
    for (const c of cards) {
      if (c.type === 'eta' && c.data) return c.data as EtaData
      if (c.type === 'journey_ready' && c.data?.eta) return c.data.eta as EtaData
    }
    return null
  },

  getParkingLots: () => {
    const state = get()
    const cards = [...state.cards].reverse()
    for (const c of cards) {
      if (c.type === 'parking' && c.data?.parking_lots?.length) {
        return c.data.parking_lots as ParkingLot[]
      }
      if (c.type === 'arriving' && c.data?.parking_lots?.length) {
        return c.data.parking_lots as ParkingLot[]
      }
    }
    return []
  },

  getRecommendedParking: () => {
    const state = get()
    const cards = [...state.cards].reverse()
    for (const c of cards) {
      if (c.type === 'journey_ready' && c.data?.parking?.lots?.length) {
        const idx = c.data.parking.recommended_index ?? 0
        return c.data.parking.lots[idx] as ParkingLot
      }
      if (c.type === 'parking' && c.data?.parking_lots?.length) {
        return c.data.parking_lots[0] as ParkingLot
      }
      if (c.type === 'arriving' && c.data?.parking_lots?.length) {
        return c.data.parking_lots[0] as ParkingLot
      }
    }
    return null
  },
}))

/**
 * Decode AMap encoded polyline string into [[lon, lat], ...] points.
 * AMap polyline encoding: uses base64-like encoding with coordinate deltas.
 * For simplicity, if the string is not encoded (contains commas/numbers), parse directly.
 * Backend sends either:
 *   - "lon,lat;lon,lat;..."  (semicolon-separated, comma-delimited)
 *   - "_encoded..."  (base64 encoded)
 * We handle the plain format directly; for encoded we return empty (graceful degradation).
 */
function decodeAmapPolyline(polyline: string): Array<[number, number]> {
  if (!polyline) return []
  // If it looks like plain "lon,lat;lon,lat" format
  if (polyline.includes(';') && polyline.includes(',')) {
    return polyline
      .split(';')
      .map((seg) => seg.split(',').map(Number))
      .filter((p) => p.length >= 2 && !isNaN(p[0]) && !isNaN(p[1]))
      .map((p) => [p[0], p[1]] as [number, number])
  }
  // If it's a single "lon,lat" pair
  if (polyline.includes(',')) {
    const [lon, lat] = polyline.split(',').map(Number)
    if (!isNaN(lon) && !isNaN(lat)) return [[lon, lat]]
  }
  // Encoded polyline — for now, return empty (backend uses plain format in our MVP)
  return []
}
