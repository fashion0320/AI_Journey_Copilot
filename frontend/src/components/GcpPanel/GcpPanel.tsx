import React, { useEffect, useState } from 'react'
import { useGcpStore } from '@/store/gcpStore'
import { chatWs } from '@/services/websocket'
import { api } from '@/services/api'
import './GcpPanel.css'

export default function GcpPanel() {
  const { context, presets, profiles, setPresets, setProfiles } = useGcpStore()
  const [activeTab, setActiveTab] = useState<'vehicle' | 'time' | 'weather' | 'traffic' | 'transit' | 'journey' | 'scenario' | 'profile'>('vehicle')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.listPresets().then(setPresets).catch(console.error)
    api.listProfiles().then(setProfiles).catch(console.error)
  }, [setPresets, setProfiles])

  const loadPreset = async (name: string) => {
    setLoading(true)
    try {
      await api.loadPreset(name)
      const ctx = await api.getGcpContext()
      useGcpStore.getState().setContext(ctx)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  const loadProfile = async (key: string) => {
    try {
      await api.loadProfile(key)
      const ctx = await api.getGcpContext()
      useGcpStore.getState().setContext(ctx)
    } catch (e) {
      console.error(e)
    }
  }

  const updateField = async (path: string, value: any) => {
    try {
      await api.updateGcpContext({ [path]: value })
      // GCP WebSocket 会推送最新快照，这里乐观更新
      useGcpStore.getState().updateFields({ [path]: value })
    } catch (e) {
      console.error(e)
    }
  }

  // 手动触发重规划（演示用）
  const triggerReplan = (reason: string) => {
    // 通过 chatWs 发送 journey_action 触发重规划需要在 in_progress 状态
    // 这里先修改 GCP 交通状态为 severe，监视器会自动检测并触发
    updateField('traffic.on_route.overall_status', 'severe')
    updateField('traffic.on_route.total_delay_min', 25)
    updateField('traffic.on_route.worst_segment_delay_min', 20)
  }

  // 重置为畅通
  const resetTraffic = () => {
    updateField('traffic.on_route.overall_status', 'smooth')
    updateField('traffic.on_route.total_delay_min', 0)
    updateField('traffic.on_route.worst_segment_delay_min', 0)
  }

  const statusLabels: Record<string, string> = {
    idle: '空闲',
    understanding: '理解意图中',
    clarifying: '澄清中',
    destination_confirm: '确认目的地',
    recommending: '推荐方案中',
    planning: '规划路线中',
    ready: '待出发',
    in_progress: '行程进行中',
    replanning: '重规划中',
    arriving: '即将到达',
    completed: '已完成',
    ended: '已结束',
  }

  // family_members 编辑 (hooks must be at top level)
  const [familyInput, setFamilyInput] = useState('')

  const tabs = [
    { key: 'vehicle', label: '🚗 车辆' },
    { key: 'time', label: '⏰ 时间' },
    { key: 'weather', label: '🌤️ 天气' },
    { key: 'traffic', label: '🚦 交通' },
    { key: 'transit', label: '✈️ 航班' },
    { key: 'journey', label: '🗺️ 旅程' },
    { key: 'scenario', label: '🎬 场景' },
    { key: 'profile', label: '👤 画像' },
  ] as const

  // Early return before accessing context properties
  if (!context) {
    return <div className="gcp-panel"><div className="gcp-loading">加载中...</div></div>
  }

  // 画像编辑 - 常去地点添加/删除
  const profile = context.user_profile
  const frequentPois = profile.travel_preferences.frequent_pois

  const addFrequentPoi = () => {
    const newPois = [...frequentPois, { name: '', address: '', lat: 0, lon: 0, tag: 'leisure' }]
    updateField('user_profile.travel_preferences.frequent_pois', newPois)
  }

  const removeFrequentPoi = (index: number) => {
    const newPois = frequentPois.filter((_: any, i: number) => i !== index)
    updateField('user_profile.travel_preferences.frequent_pois', newPois)
  }

  const updateFrequentPoi = (index: number, field: string, value: any) => {
    const newPois = frequentPois.map((p: any, i: number) =>
      i === index ? { ...p, [field]: value } : p
    )
    updateField('user_profile.travel_preferences.frequent_pois', newPois)
  }

  // Tags 编辑（餐饮/咖啡等标签数组）
  const updateTags = (path: string, tags: string[]) => {
    updateField(path, tags)
  }

  const handleTagKeyDown = (e: React.KeyboardEvent<HTMLInputElement>, path: string, currentTags: string[]) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      const val = e.currentTarget.value.trim().replace(',', '')
      if (val && !currentTags.includes(val)) {
        updateTags(path, [...currentTags, val])
      }
      e.currentTarget.value = ''
    }
  }

  const removeTag = (path: string, currentTags: string[], tag: string) => {
    updateTags(path, currentTags.filter((t: string) => t !== tag))
  }

  const addFamilyMember = () => {
    if (familyInput.trim()) {
      const newList = [...profile.family_members, familyInput.trim()]
      updateField('user_profile.family_members', newList)
      setFamilyInput('')
    }
  }
  const removeFamilyMember = (index: number) => {
    const newList = profile.family_members.filter((_: string, i: number) => i !== index)
    updateField('user_profile.family_members', newList)
  }

  return (
    <div className="gcp-panel">
      <div className="gcp-header">
        <span className="gcp-title">Global Context Panel</span>
      </div>

      <div className="gcp-tabs">
        {tabs.map((t) => (
          <button
            key={t.key}
            className={`gcp-tab ${activeTab === t.key ? 'active' : ''}`}
            onClick={() => setActiveTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="gcp-content">
        {activeTab === 'vehicle' && (
          <div className="gcp-section">
            <h4>车辆位置</h4>
            <div className="field-row">
              <label>纬度</label>
              <input
                type="number"
                step="0.0001"
                value={context.vehicle.position.lat}
                onChange={(e) => updateField('vehicle.position.lat', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>经度</label>
              <input
                type="number"
                step="0.0001"
                value={context.vehicle.position.lon}
                onChange={(e) => updateField('vehicle.position.lon', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>挡位</label>
              <select
                value={context.vehicle.gear}
                onChange={(e) => updateField('vehicle.gear', e.target.value)}
              >
                <option value="P">P (驻车)</option>
                <option value="D">D (前进)</option>
                <option value="R">R (倒车)</option>
                <option value="N">N (空挡)</option>
              </select>
            </div>
            <div className="field-row">
              <label>车速 (km/h)</label>
              <input
                type="number"
                value={context.vehicle.speed_kmh}
                onChange={(e) => updateField('vehicle.speed_kmh', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>电量/油量 (%)</label>
              <input
                type="number"
                min="0"
                max="100"
                value={context.vehicle.fuel_level_pct}
                onChange={(e) => updateField('vehicle.fuel_level_pct', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>点火状态</label>
              <input
                type="checkbox"
                checked={context.vehicle.ignition_on}
                onChange={(e) => updateField('vehicle.ignition_on', e.target.checked)}
              />
            </div>
          </div>
        )}

        {activeTab === 'time' && (
          <div className="gcp-section">
            <h4>时间上下文</h4>
            <div className="field-info">
              <span>时段</span>
              <span className="tag">{context.time.time_bucket}</span>
            </div>
            <div className="field-info">
              <span>星期</span>
              <span>第 {context.time.day_of_week} 天 {context.time.is_weekend ? '(周末)' : ''}</span>
            </div>
            <div className="field-info">
              <span>季节</span>
              <span>{context.time.season}</span>
            </div>
            <div className="field-row">
              <label>当前时间</label>
              <input
                type="datetime-local"
                value={context.time.datetime_iso?.slice(0, 16) || ''}
                onChange={(e) => {
                  const dt = new Date(e.target.value)
                  updateField('time.datetime_iso', dt.toISOString())
                  updateField('time.timestamp', dt.getTime() / 1000)
                }}
              />
            </div>
            <div className="field-row">
              <label>周末</label>
              <input
                type="checkbox"
                checked={context.time.is_weekend}
                onChange={(e) => updateField('time.is_weekend', e.target.checked)}
              />
            </div>
          </div>
        )}

        {activeTab === 'weather' && (
          <div className="gcp-section">
            <h4>天气实况</h4>
            <div className="field-row">
              <label>天气</label>
              <select
                value={context.weather.live.weather}
                onChange={(e) => updateField('weather.live.weather', e.target.value)}
              >
                <option value="晴">晴</option>
                <option value="多云">多云</option>
                <option value="阴">阴</option>
                <option value="小雨">小雨</option>
                <option value="中雨">中雨</option>
                <option value="大雨">大雨</option>
                <option value="雷阵雨">雷阵雨</option>
                <option value="小雪">小雪</option>
                <option value="雾">雾</option>
                <option value="霾">霾</option>
              </select>
            </div>
            <div className="field-row">
              <label>温度 (℃)</label>
              <input
                type="number"
                value={context.weather.live.temperature}
                onChange={(e) => updateField('weather.live.temperature', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>风力</label>
              <input
                type="text"
                value={context.weather.live.windpower}
                onChange={(e) => updateField('weather.live.windpower', e.target.value)}
                placeholder="如: 3 或 3-4"
              />
            </div>
            <div className="field-row">
              <label>城市</label>
              <input
                type="text"
                value={context.weather.city}
                onChange={(e) => updateField('weather.city', e.target.value)}
              />
            </div>
          </div>
        )}

        {activeTab === 'traffic' && (
          <div className="gcp-section">
            <h4>路线交通状态</h4>
            <div className="field-row">
              <label>整体状态</label>
              <select
                value={context.traffic.on_route.overall_status}
                onChange={(e) => updateField('traffic.on_route.overall_status', e.target.value)}
              >
                <option value="smooth">畅通</option>
                <option value="slow">缓行</option>
                <option value="congested">拥堵</option>
                <option value="severe">严重拥堵</option>
              </select>
            </div>
            <div className="field-row">
              <label>最严重路段延误 (min)</label>
              <input
                type="number"
                value={context.traffic.on_route.worst_segment_delay_min}
                onChange={(e) => updateField('traffic.on_route.worst_segment_delay_min', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>总延误 (min)</label>
              <input
                type="number"
                value={context.traffic.on_route.total_delay_min}
                onChange={(e) => updateField('traffic.on_route.total_delay_min', parseFloat(e.target.value))}
              />
            </div>
            <div className="field-row">
              <label>平均车速 (km/h)</label>
              <input
                type="number"
                value={context.traffic.on_route.avg_speed_kmh}
                onChange={(e) => updateField('traffic.on_route.avg_speed_kmh', parseFloat(e.target.value))}
              />
            </div>
            <p className="hint">提示：将状态改为 severe 可触发行程动态重规划（需 Agent 接入后生效）</p>
          </div>
        )}

        {activeTab === 'transit' && (
          <div className="gcp-section">
            <h4>航班信息</h4>
            <div className="field-row">
              <label>航班号</label>
              <input
                type="text"
                value={context.transit.flight_no}
                onChange={(e) => updateField('transit.flight_no', e.target.value)}
              />
            </div>
            <div className="field-row">
              <label>状态</label>
              <select
                value={context.transit.status}
                onChange={(e) => updateField('transit.status', e.target.value)}
              >
                <option value="scheduled">计划中</option>
                <option value="delayed">延误</option>
                <option value="boarding">登机中</option>
                <option value="departed">已起飞</option>
                <option value="arrived">已到达</option>
                <option value="cancelled">取消</option>
              </select>
            </div>
            <div className="field-row">
              <label>延误 (min)</label>
              <input
                type="number"
                value={context.transit.delay_min}
                onChange={(e) => updateField('transit.delay_min', parseInt(e.target.value) || 0)}
              />
            </div>
            <div className="field-row">
              <label>航站楼</label>
              <input
                type="text"
                value={context.transit.terminal}
                onChange={(e) => updateField('transit.terminal', e.target.value)}
              />
            </div>
            <div className="field-row">
              <label>计划到达 (STA)</label>
              <input
                type="datetime-local"
                value={context.transit.sta?.slice(0, 16) || ''}
                onChange={(e) => updateField('transit.sta', new Date(e.target.value).toISOString())}
              />
            </div>
            <div className="field-row">
              <label>预计到达 (ATA)</label>
              <input
                type="datetime-local"
                value={context.transit.ata?.slice(0, 16) || ''}
                onChange={(e) => updateField('transit.ata', new Date(e.target.value).toISOString())}
              />
            </div>
          </div>
        )}

        {activeTab === 'journey' && context.journey && (
          <div className="gcp-section">
            <h4>旅程状态（来自 AI Copilot）</h4>
            <div className="field-info">
              <span>状态</span>
              <span className="tag" style={{
                background: context.journey.status === 'in_progress' ? '#4caf50'
                  : context.journey.status === 'replanning' ? '#ff9800'
                  : context.journey.status === 'arriving' ? '#2196f3'
                  : context.journey.status === 'completed' ? '#9e9e9e'
                  : '#607d8b'
              }}>
                {statusLabels[context.journey.status] || context.journey.status}
              </span>
            </div>
            <div className="field-info">
              <span>意图类型</span>
              <span>{context.journey.intent_type || '-'}</span>
            </div>
            <div className="field-info">
              <span>用户输入</span>
              <span style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {context.journey.user_query || '-'}
              </span>
            </div>
            <div className="field-info">
              <span>目的地</span>
              <span>{context.journey.destination_name || '-'}</span>
            </div>

            <div className="field-row">
              <label>预计到达时间</label>
              <input
                type="text"
                value={context.journey.eta_arrival || ''}
                readOnly
                style={{ background: '#f5f5f5' }}
              />
            </div>
            <div className="field-row">
              <label>剩余时间 (min)</label>
              <input
                type="number"
                value={context.journey.eta_remaining_min || 0}
                readOnly
                style={{ background: '#f5f5f5' }}
              />
            </div>
            <div className="field-row">
              <label>进度 (%)</label>
              <input
                type="number"
                value={Math.round((context.journey.progress_pct || 0) * 100)}
                readOnly
                style={{ background: '#f5f5f5' }}
              />
            </div>

            <h4 style={{ marginTop: 16 }}>触发事件（演示）</h4>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <button onClick={() => triggerReplan('拥堵')} style={{ padding: '6px 12px', background: '#ff9800', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                🚦 模拟前方拥堵（触发重规划）
              </button>
              <button onClick={resetTraffic} style={{ padding: '6px 12px', background: '#4caf50', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                ✅ 恢复畅通
              </button>
              <button onClick={() => { updateField('transit.status', 'delayed'); updateField('transit.delay_min', 30) }} style={{ padding: '6px 12px', background: '#f44336', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                ✈️ 模拟航班延误30分钟
              </button>
              <button onClick={() => { updateField('weather.live.weather', '暴雨'); }} style={{ padding: '6px 12px', background: '#2196f3', color: '#fff', border: 'none', borderRadius: 4, cursor: 'pointer' }}>
                🌧️ 模拟暴雨天气
              </button>
            </div>
            <p className="hint">提示：需要在行程进行中（in_progress）触发才有效果。触发后等待约5-30秒，AI Copilot 会检测到变化并做出响应。</p>
          </div>
        )}

        {activeTab === 'scenario' && (
          <div className="gcp-section">
            <h4>预设场景</h4>
            <div className="preset-list">
              {Object.entries(presets).map(([key, desc]) => (
                <button
                  key={key}
                  className="preset-btn"
                  onClick={() => loadPreset(key)}
                  disabled={loading}
                >
                  <div className="preset-name">{key}</div>
                  <div className="preset-desc">{desc as string}</div>
                </button>
              ))}
            </div>

            <h4 style={{ marginTop: 20 }}>切换用户画像</h4>
            <div className="preset-list">
              {Object.entries(profiles).map(([key, info]) => (
                <button
                  key={key}
                  className="preset-btn"
                  onClick={() => loadProfile(key)}
                >
                  <div className="preset-name">{(info as any).name}</div>
                  <div className="preset-desc">{(info as any).occupation}</div>
                </button>
              ))}
            </div>
          </div>
        )}

        {activeTab === 'profile' && (
          <div>
            {/* 基本信息 */}
            <div className="profile-section">
              <h5>基本信息</h5>
              <div className="field-row">
                <label>姓名</label>
                <input type="text" value={profile.name}
                  onChange={(e) => updateField('user_profile.name', e.target.value)} />
              </div>
              <div className="field-row">
                <label>年龄</label>
                <input type="number" value={profile.age}
                  onChange={(e) => updateField('user_profile.age', parseInt(e.target.value) || 0)} />
              </div>
              <div className="field-row">
                <label>性别</label>
                <select value={profile.gender}
                  onChange={(e) => updateField('user_profile.gender', e.target.value)}>
                  <option value="">--</option>
                  <option value="female">女</option>
                  <option value="male">男</option>
                </select>
              </div>
              <div className="field-row">
                <label>职业</label>
                <input type="text" value={profile.occupation}
                  onChange={(e) => updateField('user_profile.occupation', e.target.value)} />
              </div>
              <div className="field-row">
                <label>城市</label>
                <input type="text" value={profile.city}
                  onChange={(e) => updateField('user_profile.city', e.target.value)} />
              </div>
            </div>

            {/* 家/公司 */}
            <div className="profile-section">
              <h5>家 / 公司</h5>
              <div className="field-row">
                <label>家庭地址</label>
                <input type="text" value={profile.home_address}
                  onChange={(e) => updateField('user_profile.home_address', e.target.value)} />
              </div>
              <div className="field-row">
                <label>家纬度</label>
                <input type="number" step="0.0001" value={profile.home_location.lat}
                  onChange={(e) => updateField('user_profile.home_location.lat', parseFloat(e.target.value))} />
              </div>
              <div className="field-row">
                <label>家经度</label>
                <input type="number" step="0.0001" value={profile.home_location.lon}
                  onChange={(e) => updateField('user_profile.home_location.lon', parseFloat(e.target.value))} />
              </div>
              <div className="field-row">
                <label>公司地址</label>
                <input type="text" value={profile.office_address}
                  onChange={(e) => updateField('user_profile.office_address', e.target.value)} />
              </div>
              <div className="field-row">
                <label>公司纬度</label>
                <input type="number" step="0.0001" value={profile.office_location.lat}
                  onChange={(e) => updateField('user_profile.office_location.lat', parseFloat(e.target.value))} />
              </div>
              <div className="field-row">
                <label>公司经度</label>
                <input type="number" step="0.0001" value={profile.office_location.lon}
                  onChange={(e) => updateField('user_profile.office_location.lon', parseFloat(e.target.value))} />
              </div>
            </div>

            {/* 出行偏好 */}
            <div className="profile-section">
              <h5>出行偏好</h5>
              <div className="field-row">
                <label>路线偏好</label>
                <select value={profile.travel_preferences.route_preference}
                  onChange={(e) => updateField('user_profile.travel_preferences.route_preference', e.target.value)}>
                  <option value="time_first">时间优先</option>
                  <option value="balance">均衡/躲避拥堵</option>
                  <option value="no_toll">不走收费路</option>
                  <option value="shortest">距离最短</option>
                </select>
              </div>
              <div className="field-row">
                <label>社交半径(km)</label>
                <input type="number" step="0.5" value={profile.travel_preferences.social_radius_km}
                  onChange={(e) => updateField('user_profile.travel_preferences.social_radius_km', parseFloat(e.target.value) || 5)} />
              </div>
              <div className="field-row">
                <label>停车偏好</label>
                <select value={profile.travel_preferences.parking_preference}
                  onChange={(e) => updateField('user_profile.travel_preferences.parking_preference', e.target.value)}>
                  <option value="convenience">便利优先</option>
                  <option value="cheap">价格优先</option>
                  <option value="balance">均衡</option>
                </select>
              </div>
              <div className="field-row">
                <label>最大绕行(min)</label>
                <input type="number" value={profile.travel_preferences.max_detour_min}
                  onChange={(e) => updateField('user_profile.travel_preferences.max_detour_min', parseInt(e.target.value) || 10)} />
              </div>
              <div className="field-row">
                <label>接受高速</label>
                <input type="checkbox" checked={profile.travel_preferences.accept_highway}
                  onChange={(e) => updateField('user_profile.travel_preferences.accept_highway', e.target.checked)} />
              </div>
            </div>

            {/* 生活偏好 */}
            <div className="profile-section">
              <h5>生活偏好</h5>
              <div className="field-row">
                <label>价格区间</label>
                <select value={profile.lifestyle_preferences.price_range}
                  onChange={(e) => updateField('user_profile.lifestyle_preferences.price_range', e.target.value)}>
                  <option value="budget">经济型</option>
                  <option value="mid">中等</option>
                  <option value="premium">品质型</option>
                  <option value="luxury">豪华</option>
                </select>
              </div>
              <div className="field-row" style={{alignItems: 'flex-start'}}>
                <label>餐饮调性</label>
                <div className="tags-input">
                  {profile.lifestyle_preferences.dining.map((tag: string) => (
                    <span key={tag} className="tag-pill">
                      {tag}
                      <button onClick={() => removeTag('user_profile.lifestyle_preferences.dining', profile.lifestyle_preferences.dining, tag)}>×</button>
                    </span>
                  ))}
                  <input type="text" placeholder="回车添加标签"
                    onKeyDown={(e) => handleTagKeyDown(e, 'user_profile.lifestyle_preferences.dining', profile.lifestyle_preferences.dining)} />
                </div>
              </div>
              <div className="field-row" style={{alignItems: 'flex-start'}}>
                <label>咖啡偏好</label>
                <div className="tags-input">
                  {profile.lifestyle_preferences.coffee.map((tag: string) => (
                    <span key={tag} className="tag-pill">
                      {tag}
                      <button onClick={() => removeTag('user_profile.lifestyle_preferences.coffee', profile.lifestyle_preferences.coffee, tag)}>×</button>
                    </span>
                  ))}
                  <input type="text" placeholder="回车添加标签"
                    onKeyDown={(e) => handleTagKeyDown(e, 'user_profile.lifestyle_preferences.coffee', profile.lifestyle_preferences.coffee)} />
                </div>
              </div>
              <div className="field-row" style={{alignItems: 'flex-start'}}>
                <label>购物偏好</label>
                <div className="tags-input">
                  {profile.lifestyle_preferences.shopping.map((tag: string) => (
                    <span key={tag} className="tag-pill">
                      {tag}
                      <button onClick={() => removeTag('user_profile.lifestyle_preferences.shopping', profile.lifestyle_preferences.shopping, tag)}>×</button>
                    </span>
                  ))}
                  <input type="text" placeholder="回车添加标签"
                    onKeyDown={(e) => handleTagKeyDown(e, 'user_profile.lifestyle_preferences.shopping', profile.lifestyle_preferences.shopping)} />
                </div>
              </div>
              <div className="field-row" style={{alignItems: 'flex-start'}}>
                <label>休闲偏好</label>
                <div className="tags-input">
                  {profile.lifestyle_preferences.leisure.map((tag: string) => (
                    <span key={tag} className="tag-pill">
                      {tag}
                      <button onClick={() => removeTag('user_profile.lifestyle_preferences.leisure', profile.lifestyle_preferences.leisure, tag)}>×</button>
                    </span>
                  ))}
                  <input type="text" placeholder="回车添加标签"
                    onKeyDown={(e) => handleTagKeyDown(e, 'user_profile.lifestyle_preferences.leisure', profile.lifestyle_preferences.leisure)} />
                </div>
              </div>
              <div className="field-row" style={{alignItems: 'flex-start'}}>
                <label>菜系偏好</label>
                <div className="tags-input">
                  {profile.lifestyle_preferences.cuisine_types.map((tag: string) => (
                    <span key={tag} className="tag-pill">
                      {tag}
                      <button onClick={() => removeTag('user_profile.lifestyle_preferences.cuisine_types', profile.lifestyle_preferences.cuisine_types, tag)}>×</button>
                    </span>
                  ))}
                  <input type="text" placeholder="回车添加标签"
                    onKeyDown={(e) => handleTagKeyDown(e, 'user_profile.lifestyle_preferences.cuisine_types', profile.lifestyle_preferences.cuisine_types)} />
                </div>
              </div>
            </div>

            {/* 常去地点 */}
            <div className="profile-section">
              <h5>常去地点</h5>
              <div className="poi-list">
                {frequentPois.map((poi: any, index: number) => (
                  <div key={index} className="poi-item">
                    <div className="poi-item-row">
                      <input type="text" placeholder="名称" value={poi.name}
                        onChange={(e) => updateFrequentPoi(index, 'name', e.target.value)} />
                      <select value={poi.tag}
                        onChange={(e) => updateFrequentPoi(index, 'tag', e.target.value)}>
                        <option value="home">家</option>
                        <option value="office">公司</option>
                        <option value="leisure">休闲</option>
                        <option value="mall">商场</option>
                        <option value="airport">机场</option>
                        <option value="hotel">酒店</option>
                      </select>
                      <button className="poi-delete-btn" onClick={() => removeFrequentPoi(index)}>删除</button>
                    </div>
                    <div className="poi-item-row">
                      <input type="text" placeholder="地址" value={poi.address}
                        onChange={(e) => updateFrequentPoi(index, 'address', e.target.value)} />
                    </div>
                    <div className="poi-item-row">
                      <input type="number" step="0.0001" placeholder="纬度" value={poi.lat}
                        onChange={(e) => updateFrequentPoi(index, 'lat', parseFloat(e.target.value) || 0)} />
                      <input type="number" step="0.0001" placeholder="经度" value={poi.lon}
                        onChange={(e) => updateFrequentPoi(index, 'lon', parseFloat(e.target.value) || 0)} />
                    </div>
                  </div>
                ))}
              </div>
              <button className="poi-add-btn" onClick={addFrequentPoi}>+ 添加常去地点</button>
            </div>

            {/* 家庭成员 */}
            <div className="profile-section">
              <h5>家庭成员</h5>
              <div className="poi-list">
                {profile.family_members.map((member: string, index: number) => (
                  <div key={index} className="poi-item" style={{flexDirection: 'row', alignItems: 'center'}}>
                    <span style={{flex: 1, color: '#e2e8f0', fontSize: 12}}>{member}</span>
                    <button className="poi-delete-btn" onClick={() => removeFamilyMember(index)}>删除</button>
                  </div>
                ))}
              </div>
              <div style={{display: 'flex', gap: 6, marginTop: 8}}>
                <input type="text" value={familyInput}
                  onChange={(e) => setFamilyInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') addFamilyMember() }}
                  placeholder="输入成员描述如「丈夫」「儿子6岁」"
                  style={{
                    flex: 1, padding: '6px 10px', background: 'rgba(255,255,255,0.05)',
                    border: '1px solid rgba(255,255,255,0.1)', borderRadius: 6,
                    color: '#e2e8f0', fontSize: 11, outline: 'none', fontFamily: 'inherit',
                  }} />
                <button className="poi-add-btn" style={{width: 'auto', marginTop: 0}} onClick={addFamilyMember}>添加</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
