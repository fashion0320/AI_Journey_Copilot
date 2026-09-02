import type { CardItem as CardItemType } from '@/types'
import ProposalCard from './ProposalCard'
import JourneyReadyCard from './JourneyReadyCard'
import RouteCard from './RouteCard'
import EtaCard from './EtaCard'
import ParkingCard from './ParkingCard'
import ArrivingCard from './ArrivingCard'
import ClarifyCard from './ClarifyCard'
import SkillStatusCard from './SkillStatusCard'
import ErrorCard from './ErrorCard'
import ThoughtProcessCard from './ThoughtProcessCard'

interface CardItemProps {
  card: CardItemType
}

/**
 * 根据卡片 type 分发渲染对应组件
 */
export default function CardItem({ card }: CardItemProps) {
  const { type, data } = card

  // data 为空时不渲染
  if (data === null || data === undefined) return null

  switch (type) {
    case 'proposals':
      // proposals 的 data 是数组
      const proposals = Array.isArray(data) ? data : []
      if (proposals.length === 0) return null
      return <ProposalCard proposals={proposals} />

    case 'journey_ready':
      return <JourneyReadyCard data={data} />

    case 'route':
      if (data?.distance_km === undefined || data?.distance_km === null) return null
      return <RouteCard data={data} />

    case 'eta':
      if (data?.remaining_min === undefined || data?.remaining_min === null) return null
      return <EtaCard data={data} />

    case 'parking':
      return <ParkingCard data={data} />

    case 'arriving':
      return <ArrivingCard data={data} />

    case 'clarify':
      return <ClarifyCard data={data} />

    case 'skill':
      return <SkillStatusCard data={data} />

    case 'error':
      return <ErrorCard data={data} />

    case 'thought_process':
      return <ThoughtProcessCard data={data} />

    default:
      return null
  }
}
