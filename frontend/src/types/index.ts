// WebSocket 消息类型
export type WsMessageType =
  // 上行
  | 'text_input'
  | 'audio_chunk'
  | 'audio_start'
  | 'audio_stop'
  | 'clarify_reply'
  | 'journey_action'
  | 'gcp_update'
  // 下行
  | 'token_stream'
  | 'thinking_stream'
  | 'thinking_end'
  | 'asr_text'
  | 'asr_final'
  | 'asr_ready'
  | 'tts_audio'
  | 'message'
  | 'skill_start'
  | 'skill_result'
  | 'card_update'
  | 'state_change'
  | 'clarify_question'
  | 'error'
  | 'ping'
  | 'pong'

export interface WsMessage<T = any> {
  type: WsMessageType
  payload: T
  request_id?: string
}

// GCP 相关类型
export interface Position {
  lat: number
  lon: number
  heading?: number | null
  address?: string | null
}

export type Gear = 'P' | 'R' | 'N' | 'D'

export interface VehicleSignals {
  position: Position
  gear: Gear
  speed_kmh: number
  fuel_level_pct: number
  ignition_on: boolean
  mileage_km: number
}

export interface CabinOccupant {
  id: string
  name: string
  seat: string
  age_group: string
  gender: string
}

export interface InCabinPerception {
  current_driver_id: string
  passengers: CabinOccupant[]
  current_behavior: string
  dangerous_behavior: string
}

export type TimeBucket =
  | 'early_morning'
  | 'morning'
  | 'late_morning'
  | 'lunch'
  | 'afternoon'
  | 'evening_rush'
  | 'dinner'
  | 'night'
  | 'late_night'

export interface TimeContext {
  datetime_iso: string
  timestamp: number
  time_bucket: TimeBucket
  day_of_week: number
  is_weekend: boolean
  is_holiday: boolean
  holiday_name: string | null
  season: string
}

export interface WeatherLive {
  province: string
  city: string
  adcode: string
  weather: string
  temperature: number
  winddirection: string
  windpower: string
  humidity: string
  reporttime: string
}

export interface WeatherForecast {
  date: string
  week: string
  dayweather: string
  nightweather: string
  daytemp: number
  nighttemp: number
  daywind: string
  nightwind: string
  daypower: string
  nightpower: string
}

export interface WeatherContext {
  city: string
  adcode: string
  live: WeatherLive
  casts: WeatherForecast[]
}

export type TrafficStatus = 'smooth' | 'slow' | 'congested' | 'severe'

export interface TrafficOnRoute {
  overall_status: TrafficStatus
  avg_speed_kmh: number
  worst_segment_desc: string
  worst_segment_delay_min: number
  total_delay_min: number
  updated_at: number
}

export interface TrafficContext {
  on_route: TrafficOnRoute
  region: {
    status: TrafficStatus
    description: string
    roads: any[]
    updated_at: number
  }
}

export type JourneyStatus =
  | 'idle'
  | 'understanding'
  | 'clarifying'
  | 'destination_confirm'
  | 'recommending'
  | 'parking_confirm'
  | 'planning'
  | 'ready'
  | 'in_progress'
  | 'replanning'
  | 'arriving'
  | 'completed'
  | 'ended'

export type IntentType = 'unknown' | 'goal_driven' | 'intent_driven'

export interface RouteWaypoint {
  name: string
  location: Position
  eta?: string | null
  duration_min: number
  distance_km: number
}

export interface JourneyRoute {
  route_id: string
  summary: string
  distance_km: number
  duration_min: number
  toll_cny: number
  waypoints: RouteWaypoint[]
  polyline_ref: string
  strategy: string
}

export interface JourneyState {
  journey_id: string
  status: JourneyStatus
  intent_type: IntentType
  user_query: string
  destination_name: string
  destination: Position
  route: JourneyRoute | null
  current_leg_index: number
  progress_pct: number
  eta_arrival: string | null
  eta_remaining_min: number
  next_stop: string | null
  started_at: string | null
  updated_at: number
}

export type FlightStatus =
  | 'scheduled'
  | 'delayed'
  | 'boarding'
  | 'departed'
  | 'arrived'
  | 'cancelled'

export interface TransitContext {
  flight_no: string
  airline: string
  departure_city: string
  departure_airport: string
  arrival_city: string
  arrival_airport: string
  terminal: string
  gate: string
  sta: string
  std: string
  ata: string
  atd: string
  status: FlightStatus
  delay_min: number
  luggage_carousel: string
  updated_at: number
}

export interface TravelPreferences {
  route_preference: string
  frequent_pois: Array<{ name: string; address: string; lat: number; lon: number; tag: string }>
  social_radius_km: number
  travel_time_pattern: string[]
  parking_preference: string
  max_detour_min: number
  accept_highway: boolean
}

export interface LifestylePreferences {
  dining: string[]
  coffee: string[]
  shopping: string[]
  leisure: string[]
  cuisine_types: string[]
  price_range: string
}

export interface UserProfile {
  id: string
  name: string
  age: number
  gender: string
  occupation: string
  city: string
  home_address: string
  home_location: Position
  office_address: string
  office_location: Position
  travel_preferences: TravelPreferences
  lifestyle_preferences: LifestylePreferences
  family_members: string[]
}

export interface GlobalContext {
  vehicle: VehicleSignals
  in_cabin: InCabinPerception
  time: TimeContext
  weather: WeatherContext
  traffic: TrafficContext
  journey: JourneyState
  transit: TransitContext
  user_profile: UserProfile
}

export interface ApiResponse<T = any> {
  code: number
  message: string
  data: T
}

// ==================== 卡片相关类型 ====================

export type CardType =
  | 'proposals'
  | 'journey_ready'
  | 'route'
  | 'eta'
  | 'parking'
  | 'arriving'
  | 'clarify'
  | 'skill'
  | 'error'
  | 'thought_process'

// 方案（proposals 卡片 data 是数组）
export interface ProposalData {
  id: string
  title: string
  summary: string
  eta_min: number
  distance_km: number
  strategy: string
  parking_hint: string
  pros: string[]
  cons: string[]
  reason: string
  source: string
}

// 行程就绪
export interface JourneyReadyData {
  destination: { name: string; lat: number; lon: number }
  route: {
    distance_km: number
    duration_min: number
    toll_cny: number
    strategy: string
    polyline: string
  }
  eta: {
    remaining_min: number
    eta_arrival_time: string
    traffic_level: string
  }
  parking: {
    lots: any[]
    recommended_index: number
  }
  reminders: any[]
}

// ETA
export interface EtaData {
  remaining_min: number
  eta_arrival_time: string
  traffic_level: string
  confidence_band_min?: number
}

// 停车
export interface ParkingLot {
  name: string
  walk_min?: number
  distance?: string
  price?: string
  available?: number
  [key: string]: any
}

export interface ParkingData {
  parking_lots: ParkingLot[]
  entry_hint?: string
}

// 到达引导
export interface ArrivingData {
  destination: string
  parking: string
  parking_lots: ParkingLot[]
  arrival_message: string
}

// POI 候选（澄清用）
export interface PoICandidate {
  id: string
  name: string
  address: string
  location: { lat: number; lon: number } | string
  distance: string
  category: string
}

// 澄清问题
export interface ClarifyData {
  question: string
  round?: number
  candidates?: PoICandidate[]
}

// Skill 状态
export type SkillStatus = 'pending' | 'success' | 'error' | 'no_result' | 'partial'

export interface SkillStatusData {
  skill: string
  action: string
  task_id: string
  status: SkillStatus
  result?: any
  error?: string
}

// 错误
export interface ErrorData {
  code: string
  message: string
}

// 思考过程
export interface ThoughtProcessData {
  content: string
}

// 通用卡片项（存在 store 中）
export interface CardItem {
  id: string
  type: CardType
  data: any
  timestamp: number
  request_id: string
}
