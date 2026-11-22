const paths = {
  explorer: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  graph: 'M10 12h4M5 7l5 4m4 0 5-4M5 17l5-4m4 0 5 4M3 3h4v4H3zM17 3h4v4h-4zM3 17h4v4H3zM17 17h4v4h-4zM10 10h4v4h-4z',
  evidence: 'M14 2H5v20h14V7l-5-5v5h5M8 12h8m-8 4h5',
  timeline: 'M3 18l6-6 4 3 8-9m-5 0h5v5',
  library: 'M3 6h7l2 2h9v12H3z',
  plus: 'M12 5v14M5 12h14',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4',
  arrow: 'M4 12h15m-6-6 6 6-6 6',
  refresh: 'M20 10a8 8 0 1 0-1 7m1-13v6h-6',
} as const

export default function WorkspaceIcon({ name }: { name: keyof typeof paths }) {
  return <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={paths[name]} />
  </svg>
}
