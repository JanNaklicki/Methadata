import { describe, expect, it } from 'vitest'
import type { ClusterRunView, SharedArtifact } from '../api/types'
import { ex } from '../test/mockApi'
import {
  artifactKey, buildArtifactNetwork, buildRelationshipNetwork, focusGroup, matchesRelationship, materialKey,
} from './graphData'

const fixture = ex.succeeded.result as unknown as ClusterRunView
const [first, second] = fixture.materials
const edge = fixture.cluster_run.edges![0]
const knownPair = fixture.pair_evidence.find(({ evidence_id }) => evidence_id === edge.pair_evidence_id)!

describe('similarity graph data', () => {
  it('shows accepted edges with both endpoints and their original evidence keys', () => {
    const parallel = { ...knownPair, evidence_id: 'parallel' }
    const { graph, groups, ungrouped } = buildRelationshipNetwork({
      ...fixture,
      pair_evidence: [...fixture.pair_evidence, parallel],
      cluster_run: {
        ...fixture.cluster_run,
        edges: [
          ...fixture.cluster_run.edges!,
          { source: 'missing', target: edge.target, pair_evidence_id: parallel.evidence_id },
          { source: edge.target, target: edge.source, pair_evidence_id: parallel.evidence_id },
          { ...edge, pair_evidence_id: 'missing-evidence' },
        ],
      },
    })
    expect(graph.order).toBe(fixture.materials.length)
    expect(graph.size).toBe(fixture.cluster_run.edges!.length + 1)
    expect(graph.extremities(edge.pair_evidence_id)).toEqual(graph.extremities(parallel.evidence_id))
    expect(graph.hasNode([...ungrouped][0])).toBe(true)
    const focused = focusGroup(graph, groups.get(fixture.clusters[0].cluster_id)!)
    expect(focused.nodes()).toEqual(fixture.clusters[0].material_ids)
    expect(focused.edges()).toContain(edge.pair_evidence_id)
    graph.forEachNode((_id, { x, y }) => {
      expect(Number.isFinite(x) && Number.isFinite(y)).toBe(true)
    })
  })

  it('combines the similarity threshold with evidence family filters', () => {
    const result = {
      ...fixture,
      pair_evidence: fixture.pair_evidence.map((pair) => pair === knownPair ? { ...pair, score: 0.75 } : pair),
      investigation: { artifacts: [], domains: {}, captured_at: {}, edge_families: { [edge.pair_evidence_id]: ['asset', 'dom'] } },
    }
    const { relationships } = buildRelationshipNetwork(result)
    const relationship = relationships.find((item) => item.pair_evidence_id === edge.pair_evidence_id)!
    expect(matchesRelationship(relationship, 75, ['text', 'asset'])).toBe(true)
    expect(matchesRelationship(relationship, 76, ['asset'])).toBe(false)
    expect(matchesRelationship(relationship, 75, ['text'])).toBe(false)
    expect(matchesRelationship(relationship, 0, [])).toBe(true)
  })
})

it('keeps shared artifacts scoped, filtered and limited to the preview', () => {
  const ids = [first.material_id, second.material_id]
  const domains: SharedArtifact[] = Array.from({ length: 9 }, (_, index) => ({
    kind: 'domain', label: `host-${index}.test`, material_ids: [...ids, 'out-of-scope', ids[0]],
  }))
  const result: ClusterRunView = {
    ...fixture,
    investigation: {
      artifacts: [...domains,
        { kind: 'tracker', label: 'shared tracker', material_ids: ids },
        { kind: 'domain', label: 'unshared.test', material_ids: [ids[0], 'out-of-scope'] },
      ],
      domains: {}, captured_at: {}, edge_families: {},
    },
  }
  const { graph, visible, candidateCount } = buildArtifactNetwork(result, new Set(ids), 'domain')
  expect(candidateCount).toBe(9)
  expect(visible).toHaveLength(8)
  expect(graph.order).toBe(10)
  expect(graph.size).toBe(16)
  expect(graph.neighbors(artifactKey(domains[0]))).toEqual(ids.map(materialKey))
  expect(buildArtifactNetwork(result, new Set([ids[0]]), '').graph.order).toBe(0)
})
