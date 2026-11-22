import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from 'react'
import type Sigma from 'sigma'
import { QUIET_COLOR, SELECTED_COLOR, type NetworkGraph } from './graphData'
import { layerNodeBorder, layerThumbnail } from './sigmaLayers'

type Renderer = Sigma<ReturnType<NetworkGraph['getNodeAttributes']>, ReturnType<NetworkGraph['getEdgeAttributes']>>

interface Props {
  graph: NetworkGraph
  label: string
  children: () => ReactNode
  onNode: (key: string) => void
  onEdge?: (key: string, opener: HTMLElement) => void
  fitRequest?: number
  suggestedCount?: number
  showSuggested?: boolean
  onToggleSuggested?: () => void
}

export default function GraphCanvas({
  graph, label, children, onNode, onEdge, fitRequest, suggestedCount = 0, showSuggested = true, onToggleSuggested,
}: Props) {
  const container = useRef<HTMLDivElement>(null)
  const renderer = useRef<Renderer>(null)
  const hoveredNode = useRef<string | null>(null)
  const showThumbsRef = useRef(true)
  const showEdgeLabelsRef = useRef(false)
  const [available, setAvailable] = useState<boolean>()
  const [listOpen, setListOpen] = useState(false)
  const [showThumbs, setShowThumbs] = useState(true)
  const [showEdgeLabels, setShowEdgeLabels] = useState(false)
  const getGraph = useEffectEvent(() => graph)
  const selectNode = useEffectEvent(onNode)
  const openEdge = useEffectEvent((key: string, opener: HTMLElement) => onEdge?.(key, opener))
  const edgesInteractive = Boolean(onEdge)

  useEffect(() => {
    const stage = container.current!
    if (typeof WebGL2RenderingContext === 'undefined') {
      setAvailable(false)
      setListOpen(true)
      return
    }

    let disposed = false
    async function start() {
      const [
        { default: Sigma, DEFAULT_STYLES },
        { DEFAULT_PRIMITIVES },
        { layerDashed, layerFill, layerPlain, pathCurved, sdfDiamond, sdfSquare, sdfTriangle },
      ] = await Promise.all([
        import('sigma'),
        import('sigma/primitives'),
        import('sigma/rendering'),
      ])
      if (disposed) return
      const instance = new Sigma(getGraph(), stage, {
        primitives: {
          ...DEFAULT_PRIMITIVES,
          nodes: {
            ...DEFAULT_PRIMITIVES.nodes,
            shapes: [
              ...(DEFAULT_PRIMITIVES.nodes.shapes ?? []),
              sdfSquare(),
              sdfDiamond(),
              sdfTriangle(),
            ],
            layers: [layerFill(), layerThumbnail(), layerNodeBorder()],
            variables: {
              image: { type: 'string', default: '' },
              borderColor: { type: 'color', default: '#7ce2ff' },
              borderSize: { type: 'number', default: 2.2 },
            },
          },
          edges: {
            ...DEFAULT_PRIMITIVES.edges,
            paths: [...(DEFAULT_PRIMITIVES.edges.paths ?? []), pathCurved()],
            layers: [
              layerPlain({ color: { attribute: 'plainColor', default: '#78b4ff' } }),
              layerDashed({
                dashColor: { attribute: 'color' },
                dashSize: { attribute: 'dashSize', default: 0, mode: 'pixels' },
                gapSize: { attribute: 'gapSize', default: 0, mode: 'pixels' },
                cap: 'round',
              }),
            ],
            variables: {
              plainColor: { type: 'color', default: '#78b4ff' },
              dashSize: { type: 'number', default: 0 },
              gapSize: { type: 'number', default: 0 },
            },
          },
        },
        settings: {
          allowInvalidContainer: true,
          enableEdgeEvents: edgesInteractive,
          renderEdgeLabels: true,
          enableCameraRotation: false,
          nodeLabelEvents: 'extend',
          gestureTarget: 'shared',
          sharedGestureWheelMessage: 'Użyj Ctrl i przewijania, aby przybliżyć graf',
          sharedGestureAppleWheelMessage: 'Użyj ⌘ i przewijania, aby przybliżyć graf',
          sharedGestureTouchMessage: 'Użyj dwóch palców, aby przesunąć lub przybliżyć graf',
          itemSizesReference: 'screen',
          autoRescale: 'once',
          stagePadding: 84,
          minCameraRatio: 0.08,
          minEdgeThickness: 1.2,
          maxCameraRatio: 4,
          labelGridCellSize: 64,
          edgePickingPadding: 6,
        },
        styles: {
          stage: { cursor: { whenState: 'isPanning', then: 'grabbing', else: 'grab' } },
          nodes: [
            {
              ...DEFAULT_STYLES.nodes,
              shape: { attribute: 'shape', defaultValue: 'circle' },
              image: { attribute: 'image', defaultValue: '' },
              borderColor: { attribute: 'borderColor', defaultValue: '#7ce2ff' },
              borderSize: { attribute: 'borderSize', defaultValue: 2.4 },
              labelFont: 'Inter, system-ui',
              labelSize: 12.5,
              labelPosition: 'below',
              labelColor: '#eef4fc',
              labelBackgroundColor: '#0a111de6',
              labelBackgroundPadding: 4,
              backdropColor: '#122033',
              backdropPadding: 6,
              backdropCornerRadius: 6,
              backdropBorderColor: '#7ce2ff',
              backdropBorderWidth: 1,
              backdropShadowColor: '#00000090',
              backdropShadowBlur: 12,
              cursor: 'pointer',
              labelCursor: 'pointer',
            },
            {
              whenData: 'muted',
              then: {
                opacity: 0.34,
                borderColor: QUIET_COLOR,
                labelColor: '#8d9cb0',
              },
            },
            {
              whenData: 'emphasized',
              then: {
                borderColor: '#f4c37b',
                borderSize: 3.4,
                labelVisibility: 'visible',
              },
            },
            {
              whenState: 'isHovered',
              then: {
                borderColor: '#cef2ff',
                borderSize: 3.6,
                labelVisibility: 'visible',
                depth: 'topNodes',
                backdropVisibility: 'visible',
                backdropBorderColor: '#cef2ff',
                backdropBorderWidth: 1.5,
              },
            },
            {
              whenData: 'selected',
              then: {
                borderColor: SELECTED_COLOR,
                borderSize: 4,
                labelVisibility: 'visible',
                depth: 'topNodes',
                backdropVisibility: 'visible',
                backdropBorderColor: SELECTED_COLOR,
                backdropBorderWidth: 1.5,
              },
            },
          ],
          edges: [
            {
              ...DEFAULT_STYLES.edges,
              parallelPath: 'curved',
              plainColor: { attribute: 'plainColor', defaultValue: '#78b4ff' },
              dashSize: { attribute: 'dashSize', defaultValue: 0 },
              gapSize: { attribute: 'gapSize', defaultValue: 0 },
              opacity: { attribute: 'opacity', defaultValue: 0.72 },
              label: { attribute: 'label', defaultValue: '' },
              labelFont: 'JetBrains Mono, Inter, monospace',
              labelSize: 11,
              labelColor: '#eef5ff',
              labelBackgroundColor: '#0a1220f2',
              labelBackgroundPadding: 3,
              labelVisibility: 'hidden',
              cursor: edgesInteractive ? 'pointer' : 'inherit',
            },
            {
              whenData: 'showLabel',
              then: { labelVisibility: 'visible', opacity: 0.96 },
            },
            {
              whenData: 'selected',
              then: {
                opacity: 1,
                labelVisibility: 'visible',
                labelColor: '#62daca',
              },
            },
            {
              whenData: 'muted',
              then: { opacity: 0.14, labelVisibility: 'hidden' },
            },
            {
              whenState: 'isHovered',
              then: {
                opacity: 1,
                labelVisibility: 'visible',
                labelColor: '#cef2ff',
              },
            },
            { whenData: 'hidden', then: { visibility: 'hidden' } },
          ],
        },
        nodeReducer: (node, data) => {
          const out = { ...data } as Record<string, unknown>
          if (!showThumbsRef.current) {
            out.image = ''
          }
          const hovered = hoveredNode.current
          if (hovered) {
            const g = renderer.current?.getGraph() ?? getGraph()
            const isHovered = node === hovered
            const isNeighbor = !isHovered && g.hasNode(hovered) && g.areNeighbors(node, hovered)
            if (isHovered || isNeighbor) {
              out.opacity = 1
              out.labelVisibility = 'visible'
              if (isNeighbor) {
                out.borderSize = 3.2
              }
            } else {
              out.opacity = 0.2
              out.labelVisibility = 'hidden'
            }
          }
          return out
        },
        edgeReducer: (edge, data) => {
          const out = { ...data } as Record<string, unknown>
          if (out.visibility === 'hidden') return out
          const hovered = hoveredNode.current
          const g = renderer.current?.getGraph() ?? getGraph()
          if (hovered && g.hasEdge(edge)) {
            const [s, t] = g.extremities(edge)
            if (s === hovered || t === hovered) {
              out.opacity = 1
              out.labelVisibility = 'visible'
              out.size = Math.max(Number(out.size ?? 2) + 1.2, 3.2)
            } else {
              out.opacity = 0.08
              out.labelVisibility = 'hidden'
            }
          } else if (showEdgeLabelsRef.current && g.hasEdge(edge) && !g.getEdgeAttribute(edge, 'muted')) {
            out.labelVisibility = 'visible'
          }
          return out
        },
      }) as unknown as Renderer

      instance.on('clickNode', ({ node }) => selectNode(node))
      instance.on('enterNode', ({ node }) => {
        hoveredNode.current = node
        instance.refresh()
      })
      instance.on('leaveNode', () => {
        hoveredNode.current = null
        instance.refresh()
      })
      if (edgesInteractive) instance.on('clickEdge', ({ edge }) => {
        if (!instance.getGraph().getEdgeAttribute(edge, 'hidden')) openEdge(edge, stage)
      })
      renderer.current = instance
      setAvailable(true)
    }

    void start().catch((err) => {
      if (disposed) return
      console.error('[GraphCanvas] Sigma init failed:', err)
      // Sigma creates its canvas before it can detect that WebGL is unavailable.
      stage.querySelectorAll('canvas').forEach((canvas) => {
        canvas.getContext('webgl2')?.getExtension('WEBGL_lose_context')?.loseContext()
      })
      stage.replaceChildren()
      setAvailable(false)
      setListOpen(true)
    })

    return () => {
      disposed = true
      hoveredNode.current = null
      renderer.current?.kill()
      renderer.current = null
    }
  }, [edgesInteractive])

  useEffect(() => {
    const instance = renderer.current
    if (!instance) return
    hoveredNode.current = null
    instance.setGraph(graph)
    void instance.getCamera().reset({ duration: 0 })
  }, [graph, fitRequest])

  useEffect(() => {
    showThumbsRef.current = showThumbs
    showEdgeLabelsRef.current = showEdgeLabels
    renderer.current?.refresh()
  }, [showThumbs, showEdgeLabels])

  function moveCamera(action: 'zoomIn' | 'zoomOut' | 'reset') {
    const duration = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180
    void renderer.current?.getCamera()[action]({ duration })
  }

  return <>
    <div className="network-stage" hidden={available === false}>
      <div ref={container} className="network-canvas" role="group" aria-label={label} tabIndex={-1} />
      {available && <div className="network-camera" role="group" aria-label="Sterowanie grafem">
        <button
          type="button"
          className="outline"
          aria-pressed={showThumbs}
          title="Pokaż lub ukryj miniatury w węzłach"
          onClick={() => setShowThumbs((v) => !v)}
        >
          Miniatury
        </button>
        {edgesInteractive && <button
          type="button"
          className="outline"
          aria-pressed={showEdgeLabels}
          title="Pokaż procent podobieństwa na wszystkich połączeniach"
          onClick={() => setShowEdgeLabels((v) => !v)}
        >
          Wagi %
        </button>}
        {suggestedCount > 0 && onToggleSuggested && <button
          type="button"
          className="outline"
          aria-pressed={showSuggested}
          title="Pokaż lub ukryj sugerowane połączenia (linie przerywane)"
          onClick={onToggleSuggested}
        >
          Sugerowane ({suggestedCount})
        </button>}
        <button type="button" className="outline" aria-label="Przybliż graf" onClick={() => moveCamera('zoomIn')}>+</button>
        <button type="button" className="outline" aria-label="Oddal graf" onClick={() => moveCamera('zoomOut')}>−</button>
        <button type="button" className="outline" onClick={() => moveCamera('reset')}>Dopasuj</button>
      </div>}
    </div>
    {available === false && <p className="network-fallback muted" role="status">Ta przeglądarka nie obsługuje WebGL 2, więc graf się nie wyświetla. Te same dane znajdziesz na liście poniżej.</p>}
    <details className="network-list" open={listOpen} onToggle={(event) => setListOpen(event.currentTarget.open)}>
      <summary>Lista z grafu</summary>
      {listOpen && children()}
    </details>
  </>
}
