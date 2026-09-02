import { create } from 'zustand'
import type { GlobalContext, JourneyStatus } from '@/types'

interface GcpState {
  context: GlobalContext | null
  presets: Record<string, string>
  profiles: Record<string, { id: string; name: string; age: number; occupation: string }>
  showPanel: boolean
  setContext: (ctx: GlobalContext) => void
  setPresets: (p: Record<string, string>) => void
  setProfiles: (p: Record<string, any>) => void
  togglePanel: () => void
  setShowPanel: (show: boolean) => void
  updateFields: (fields: Record<string, any>) => void
}

export const useGcpStore = create<GcpState>((set) => ({
  context: null,
  presets: {},
  profiles: {},
  showPanel: true,

  setContext: (ctx) => set({ context: ctx }),
  setPresets: (p) => set({ presets: p }),
  setProfiles: (p) => set({ profiles: p }),
  togglePanel: () => set((s) => ({ showPanel: !s.showPanel })),
  setShowPanel: (show) => set({ showPanel: show }),
  updateFields: (fields) =>
    set((state) => {
      if (!state.context) return {}
      // 简单的字段级更新（扁平路径）
      const newCtx = JSON.parse(JSON.stringify(state.context)) as GlobalContext
      for (const [path, value] of Object.entries(fields)) {
        const parts = path.split('.')
        let cur: any = newCtx
        for (let i = 0; i < parts.length - 1; i++) {
          if (cur[parts[i]] === undefined) cur[parts[i]] = {}
          cur = cur[parts[i]]
        }
        cur[parts[parts.length - 1]] = value
      }
      return { context: newCtx }
    }),
}))
