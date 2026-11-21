// Mock API oparty na przykładach W01 (contracts/http/examples). Włączany: MOCK_API=1 npm run dev.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { Plugin } from 'vite'

const dir = resolve(import.meta.dirname, '../contracts/http/examples')
const ex = (name: string) => readFileSync(resolve(dir, `${name}.json`), 'utf8')

export function mockApi(): Plugin {
  const polls = new Map<string, number>()
  return {
    name: 'methadata-mock-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0]
        if (!url.startsWith('/api/v1/')) return next()
        const send = (status: number, body: string) => {
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.end(body)
        }
        if (url === '/api/v1/health') return send(200, ex('health_ok'))
        if (url === '/api/v1/demo') return send(200, ex('demo'))
        if (url === '/api/v1/observations' && req.method === 'POST') {
          const json = (req.headers['content-type'] ?? '').includes('json')
          return send(201, ex(json ? 'observation_created_url' : 'observation_created_file'))
        }
        if (url === '/api/v1/runs' && req.method === 'POST') return send(202, ex('run_accepted'))
        const run = url.match(/^\/api\/v1\/runs\/([^/]+)$/)
        if (run) {
          const n = (polls.get(run[1]) ?? 0) + 1
          polls.set(run[1], n)
          return send(200, ex(n === 1 ? 'run_queued' : n === 2 ? 'run_running' : 'run_succeeded_complete'))
        }
        const ev = url.match(/^\/api\/v1\/runs\/[^/]+\/evidence\/([^/]+)$/)
        if (ev) return send(200, ex(ev[1] === 'pev-939f4e5676461047' ? 'evidence_unavailable' : 'evidence_full'))
        send(404, ex('error_run_not_found'))
      })
    },
  }
}
