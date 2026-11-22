import { collectionPath, parseRoute, runPath, withParams } from './routes'

describe('adresy', () => {
  it('rozpoznaje widoki bazy i ich parametry', () => {
    const route = parseRoute('/collection/graph?group=cl-1&graph=artifacts&material=can-1')
    expect(route.collection).toBe(true)
    expect(route.view).toBe('graph')
    expect(route.params.get('group')).toBe('cl-1')
    expect(parseRoute('/collection/files').view).toBe('library')
    expect(parseRoute('/collection').view).toBe('explorer')
  })

  it('stary adres raportu importu przechodzi na ?import=', () => {
    const route = parseRoute('/collection/imports/abc123')
    expect(route.view).toBe('explorer')
    expect(route.params.get('import')).toBe('abc123')
  })

  it('buduje adresy odwracające parsowanie', () => {
    const params = withParams(new URLSearchParams(), { q: 'p1', group: 'cl-1', pusty: '', usun: null })
    expect(collectionPath('timeline', params)).toBe('/collection/timeline?q=p1&group=cl-1')
    expect(collectionPath('explorer')).toBe('/collection')
    expect(runPath('run 1', 'pev/2', new URLSearchParams({ widok: 'graf' }))).toBe('/runs/run%201/evidence/pev%2F2?widok=graf')
    expect(parseRoute(runPath('run 1', 'pev/2')).evidenceId).toBe('pev/2')
  })

  it('withParams usuwa parametr przy null lub pustej wartości', () => {
    const next = withParams(new URLSearchParams('a=1&b=2'), { a: null, b: '' })
    expect(next.toString()).toBe('')
  })
})
