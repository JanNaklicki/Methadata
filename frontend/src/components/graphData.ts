import { MultiUndirectedGraph } from 'graphology'
import forceAtlas2 from 'graphology-layout-forceatlas2'
import type { ClusterRunView, SharedArtifact } from '../api/types'
import { formatScore } from './formatScore'

export const MATERIAL_COLOR = '#203247'
export const MATERIAL_RING_COLOR = '#7ce2ff'
export const UNGROUPED_COLOR = '#1a2535'
export const UNGROUPED_RING_COLOR = '#647991'
export const SELECTED_COLOR = '#62daca'
export const QUIET_COLOR = '#3a495c'
export const SUGGESTED_COLOR = '#f4c37b'

export const CLUSTER_PALETTE = [
  '#7ce2ff', // cyan
  '#5fe3ce', // teal
  '#f4c37b', // amber
  '#c4a5f7', // violet
  '#ff9382', // coral
  '#9ee493', // mint
  '#8ab6ff', // sky
  '#f29ec8', // rose
]

export const EDGE_COLORS: Record<string, string> = {
  asset: '#78b4ff', text: '#f4be6b', dom: '#c49ef8',
  domain: '#7ce2ff', tracker: '#f4be6b', image: '#78b4ff', script: '#c49ef8',
  person: '#ff9382', brand: '#5fe3ce', contact: '#9ee493', affiliate: '#f29ec8', infrastructure: '#8ab6ff',
}

interface Point { x: number; y: number }
interface Node extends Point {
  label: string
  size: number
  baseSize?: number
  color: string
  borderColor?: string
  borderSize?: number
  image?: string
  muted?: boolean
  selected?: boolean
  emphasized?: boolean
  hoveredNeighbor?: boolean
  shape?: 'circle' | 'square' | 'diamond' | 'triangle'
}
interface Edge {
  color: string
  plainColor?: string
  dashSize?: number
  gapSize?: number
  size: number
  label?: string
  score?: number
  opacity?: number
  suggested?: boolean
  hidden?: boolean
  muted?: boolean
  selected?: boolean
  showLabel?: boolean
}

export type NetworkGraph = MultiUndirectedGraph<Node, Edge>

export function edgeThickness(score: number) {
  const clamped = Math.max(0, Math.min(1, score))
  return Number((1.2 + Math.pow(clamped, 2.2) * 7.2).toFixed(2))
}

export function edgeOpacity(score: number) {
  const clamped = Math.max(0, Math.min(1, score))
  return Number((0.42 + clamped * 0.53).toFixed(2))
}

export function nodeRadius(degree: number, grouped: boolean, hasImage: boolean) {
  if (!grouped) return hasImage ? 13 : 10
  return Math.round(15 + Math.min(11, Math.sqrt(Math.max(1, degree)) * 3.8))
}

export function compactLabel(raw: string, max = 28) {
  const text = raw.trim()
  return text.length > max ? text.slice(0, max - 1).trimEnd() + '…' : text
}

export function acceptedRelationships(result: ClusterRunView) {
  const materials = new Set(result.materials.map((item) => item.material_id))
  const evidence = new Map(result.pair_evidence.map((item) => [item.evidence_id, item]))
  const seen = new Set<string>()
  return (result.cluster_run.edges ?? []).flatMap((edge) => {
    const pair = evidence.get(edge.pair_evidence_id)
    if (!pair || seen.has(edge.pair_evidence_id) || !materials.has(edge.source) || !materials.has(edge.target)) return []
    seen.add(edge.pair_evidence_id)
    return [{ ...edge, score: pair.score ?? edge.weight ?? 0, families: result.investigation?.edge_families[edge.pair_evidence_id] ?? [] }]
  })
}

export type Relationship = ReturnType<typeof acceptedRelationships>[number]

export function suggestedRelationships(result: ClusterRunView) {
  const materials = new Set(result.materials.map((item) => item.material_id))
  const acceptedKeys = new Set(
    (result.cluster_run.edges ?? []).map((e) => (e.source <= e.target ? `${e.source}\0${e.target}` : `${e.target}\0${e.source}`)),
  )
  const seen = new Set<string>()
  return (result.investigation?.suggested_edges ?? []).filter((edge) => {
    if (!materials.has(edge.source) || !materials.has(edge.target)) return false
    const pairKey = edge.source <= edge.target ? `${edge.source}\0${edge.target}` : `${edge.target}\0${edge.source}`
    if (acceptedKeys.has(pairKey) || seen.has(edge.pair_evidence_id)) return false
    seen.add(edge.pair_evidence_id)
    return true
  })
}

export type SuggestedRelationship = ReturnType<typeof suggestedRelationships>[number]

export function matchesRelationship(edge: Relationship, threshold: number, families: string[]) {
  return edge.score >= threshold / 100 && (!families.length || families.some((family) => edge.families.includes(family)))
}

export function relationshipColor(edge: Relationship, families: string[]) {
  const family = edge.families.find((name) => families.includes(name)) ?? edge.families[0]
  return EDGE_COLORS[family] ?? MATERIAL_RING_COLOR
}

function placeGroups(groups: string[][], graph: NetworkGraph) {
  // Lay out each group independently, then pack its rectangle onto the canvas.
  const gap = 2.6
  const boxes = groups.map((ids) => {
    const group = focusGroup(graph, ids)
    ids.forEach((id, index) => {
      const angle = index * Math.PI * 2 / ids.length
      group.mergeNodeAttributes(id, { x: Math.cos(angle), y: Math.sin(angle) })
    })
    if (group.size && ids.length > 2) {
      forceAtlas2.assign(group, {
        iterations: ids.length > 150 ? 40 : 80,
        getEdgeWeight: null,
        settings: {
          barnesHutOptimize: ids.length > 80,
          gravity: 0.65,
          scalingRatio: 2.2,
          adjustSizes: true,
        },
      })
    }
    if (!group.size) {
      const columns = Math.ceil(Math.sqrt(ids.length * 1.5))
      const points = ids.map((id, index) => ({ id, x: (index % columns) * 1.6, y: -Math.floor(index / columns) * 1.5 }))
      return { width: Math.min(columns, ids.length) * 1.6, height: Math.ceil(ids.length / columns) * 1.5, points }
    }
    const points = ids.map((id) => ({ id, ...group.getNodeAttributes(id) }))
    const extent = points.reduce((bounds, { x, y }) => ({
      minX: Math.min(bounds.minX, x), maxX: Math.max(bounds.maxX, x),
      minY: Math.min(bounds.minY, y), maxY: Math.max(bounds.maxY, y),
    }), { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity })
    const spanX = extent.maxX - extent.minX
    const spanY = extent.maxY - extent.minY
    const scale = Math.max(2.3, 2.25 * Math.sqrt(ids.length)) / Math.max(spanX, spanY, 0.001)
    return {
      width: Math.max(1.4, spanX * scale),
      height: Math.max(1.4, spanY * scale),
      points: points.map(({ id, x, y }) => ({ id, x: (x - extent.minX) * scale, y: (y - extent.maxY) * scale })),
    }
  })
  const rowWidth = Math.sqrt(1.65 * boxes.reduce((area, box) => area + (box.width + gap) * (box.height + gap), 0))
  let x = 0
  let y = 0
  let rowHeight = 0
  boxes.forEach((box) => {
    if (x && x + box.width > rowWidth) {
      x = 0
      y -= rowHeight + gap
      rowHeight = 0
    }
    box.points.forEach((point) => graph.mergeNodeAttributes(point.id, { x: x + point.x, y: y + point.y }))
    x += box.width + gap
    rowHeight = Math.max(rowHeight, box.height)
  })
}

export function buildRelationshipNetwork(result: ClusterRunView) {
  const graph = new MultiUndirectedGraph<Node, Edge>()
  const relationships = acceptedRelationships(result)
  const remaining = new Set(result.materials.map((item) => item.material_id))
  const clusterColorOf = new Map<string, string>()
  const groups = new Map(result.clusters.map((cluster, idx) => {
    const members = cluster.material_ids.filter((id) => remaining.delete(id))
    const color = CLUSTER_PALETTE[idx % CLUSTER_PALETTE.length]
    members.forEach((id) => clusterColorOf.set(id, color))
    return [cluster.cluster_id, members]
  }))

  const degrees = new Map<string, number>()
  relationships.forEach((edge) => {
    degrees.set(edge.source, (degrees.get(edge.source) ?? 0) + 1)
    degrees.set(edge.target, (degrees.get(edge.target) ?? 0) + 1)
  })

  const phylo = result.investigation?.phylofake
  const patientZeros = new Set(Object.values(phylo?.clusters ?? {}).map((c) => c.patient_zero))

  result.materials.forEach((material) => {
    const grouped = !remaining.has(material.material_id)
    const image = material.thumbnail?.href ?? ''
    const isRoot = patientZeros.has(material.material_id)
    const ring = isRoot ? '#f4c37b' : (clusterColorOf.get(material.material_id) ?? (grouped ? MATERIAL_RING_COLOR : UNGROUPED_RING_COLOR))
    const baseSize = nodeRadius(degrees.get(material.material_id) ?? 0, grouped, Boolean(image)) + (isRoot ? 3 : 0)
    graph.addNode(material.material_id, {
      x: 0,
      y: 0,
      color: grouped ? MATERIAL_COLOR : UNGROUPED_COLOR,
      borderColor: ring,
      borderSize: isRoot ? 3.5 : grouped ? 2.6 : 1.8,
      emphasized: isRoot,
      image,
      label: `${isRoot ? '★ ' : ''}${compactLabel(material.label)}`,
      size: baseSize,
      baseSize,
    })
  })
  relationships.forEach((edge) => {
    const [source, target] = edge.source <= edge.target ? [edge.source, edge.target] : [edge.target, edge.source]
    const color = relationshipColor(edge, [])
    const mutShort = phylo?.edges[edge.pair_evidence_id]?.mutation_short
    graph.addUndirectedEdgeWithKey(edge.pair_evidence_id, source, target, {
      size: edgeThickness(edge.score),
      opacity: edgeOpacity(edge.score),
      score: edge.score,
      label: mutShort ? `${formatScore(edge.score)} · ${mutShort}` : formatScore(edge.score),
      color,
      plainColor: color,
      dashSize: 0,
      gapSize: 0,
      suggested: false,
    })
  })
  placeGroups([...groups.values(), [...remaining]].filter((ids) => ids.length), graph)
  const suggested = suggestedRelationships(result)
  suggested.forEach((edge) => {
    const [source, target] = edge.source <= edge.target ? [edge.source, edge.target] : [edge.target, edge.source]
    if (graph.hasEdge(edge.pair_evidence_id)) return
    graph.addUndirectedEdgeWithKey(edge.pair_evidence_id, source, target, {
      size: Math.min(3.4, Math.max(1.8, edgeThickness(edge.score) * 0.55)),
      opacity: 0.72,
      score: edge.score,
      label: `${formatScore(edge.score)} ?`,
      color: SUGGESTED_COLOR,
      plainColor: '#00000000',
      dashSize: 7,
      gapSize: 6,
      suggested: true,
    })
  })
  return { graph, groups, relationships, suggested, ungrouped: remaining }
}

export function focusGroup(graph: NetworkGraph, ids: string[]) {
  const group = graph.nullCopy()
  ids.forEach((id) => group.addNode(id, { ...graph.getNodeAttributes(id) }))
  ids.forEach((id) => graph.forEachEdge(id, (key, attributes, source, target) => {
    if (source === id && group.hasNode(target)) group.addUndirectedEdgeWithKey(key, source, target, { ...attributes })
  }))
  return group
}

export function artifactKey(item: SharedArtifact) {
  return JSON.stringify([item.kind, item.label])
}

const ARTIFACT_SHAPES: Record<SharedArtifact['kind'], Node['shape']> = {
  domain: 'diamond',
  tracker: 'triangle',
  image: 'square',
  script: 'square',
  person: 'circle',
  brand: 'diamond',
  contact: 'triangle',
  affiliate: 'triangle',
  infrastructure: 'square',
}

export function buildArtifactNetwork(result: ClusterRunView, materialIds: Set<string>, kind: string) {
  const graph = new MultiUndirectedGraph<Node, Edge>()
  const inScope = new Set(result.materials.filter((item) => materialIds.has(item.material_id)).map((item) => item.material_id))
  const candidates = (result.investigation?.artifacts ?? [])
    .filter((item) => !kind || item.kind === kind)
    .map((item) => ({ ...item, material_ids: [...new Set(item.material_ids.filter((id) => inScope.has(id)))] }))
    .filter((item) => item.material_ids.length > 1)
    .sort((a, b) => b.material_ids.length - a.material_ids.length)
  const visible = candidates.slice(0, 8)
  const linked = new Set(visible.flatMap((item) => item.material_ids))
  const materials = result.materials.filter((item) => linked.has(item.material_id))
  const height = Math.max(4.5, Math.sqrt(materials.length) * 2.4, visible.length * 1.1)
  const y = (index: number, count: number) => count === 1 ? 0 : height * (0.5 - index / (count - 1))
  materials.forEach((material, index) => {
    const image = material.thumbnail?.href ?? ''
    graph.addNode(materialKey(material.material_id), {
      x: -3.2,
      y: y(index, materials.length),
      label: compactLabel(material.label),
      color: MATERIAL_COLOR,
      borderColor: MATERIAL_RING_COLOR,
      borderSize: 2.4,
      image,
      size: image ? 16 : 12,
      baseSize: image ? 16 : 12,
    })
  })
  visible.forEach((item, index) => {
    const key = artifactKey(item)
    const color = EDGE_COLORS[item.kind]
    graph.addNode(key, {
      x: 3.2,
      y: y(index, visible.length),
      label: artifactLabel(item),
      color: '#172334',
      borderColor: color,
      borderSize: 2.8,
      size: 14,
      baseSize: 14,
      shape: ARTIFACT_SHAPES[item.kind] ?? 'square',
    })
    item.material_ids.forEach((id) => graph.addUndirectedEdge(materialKey(id), key, {
      color, plainColor: color, dashSize: 0, gapSize: 0, size: 2.4, opacity: 0.72,
    }))
  })
  return { graph, visible, materials, candidateCount: candidates.length }
}

export function materialKey(id: string) {
  return JSON.stringify([id])
}

export function artifactLabel(item: SharedArtifact) {
  return item.kind === 'image' || item.kind === 'script' ? item.label.slice(0, 12) + '…' : item.label
}
