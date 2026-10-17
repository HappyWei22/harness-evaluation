// Local context-export extension; modified from upstream. See MODIFICATIONS.md.
import assert from 'node:assert/strict'
import { afterEach, describe, test, vi } from 'vitest'
import { assemble } from '../../src/client/assemble'
import { buildContextExport, downloadJson, exportFilename, makeExportReader } from '../../src/client/contextExport'
import type { LogEvent } from '../../src/client/contextExport'
import { req, tl } from './components/browserHarness'
import { surfaceNodeAt } from './helpers/kit'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const event = (seq: number, type = 'user/message'): LogEvent => ({
  seq, type, data: { content: [{ type: 'text', text: `full body ${seq}\n预算：4500` }] },
})

describe('selected context evidence export', () => {
  test('exports selected pre-response nodes including removed history, never the selected response', async () => {
    const request = req({ seq: 30, turn: 3, step: 1, prompt: 7800 })
    const data = tl({
      requests: [request],
      nodes: [surfaceNodeAt(20), surfaceNodeAt(30, { cat: 'assistant' }), surfaceNodeAt(40)],
      archive: [surfaceNodeAt(10, { gone: 35 })],
      systems: [{ seq: 2, time: 1, tokens: 3 }],
      events: [{ seq: 15, time: 1, kind: 'inject' }, { seq: 38, time: 1, kind: 'prune' }],
    })
    const view = assemble(data, { headers: [{ seq: 3, time: 1, tools: [] }] }, request.seq)
    const read = vi.fn(async (seq: number) => event(seq))
    const result = await buildContextExport({ sessionId: 'session-test', view, request, data, read, version: '0.72.0', exportedAt: 'fixed' })
    assert.deepEqual(result.nodes.map(n => n.metadata.seq), [10, 20])
    assert.deepEqual(read.mock.calls.map(c => c[0]), [2, 3, 10, 20])
    assert.deepEqual(result.contextEvents.map(e => e.seq), [15])
    assert.equal(result.nodes[0].event?.data.content !== undefined, true)
    assert.equal(result.exportedAt, 'fixed')
    assert.equal(result.evidence.actualProviderRequestCaptured, false)
    assert.equal(result.selection.mode, 'historical_request')
    assert.equal(result.tokenStatistics.providerReportedPromptTokens, 7800)
    assert.equal(result.coverage.allListedEventsFetched, true)
  })

  test('records missing, mismatched and failed reads without fabricating content or losing successful rows', async () => {
    const data = tl({ nodes: [1, 2, 3, 4].map(seq => surfaceNodeAt(seq)), droppedNodes: 5, archiveFloor: 99 })
    const view = assemble(data, null, 10)
    const read = async (seq: number) => {
      if (seq === 1) return null
      if (seq === 2) throw new Error('permission denied')
      if (seq === 3) return event(900)
      return event(seq)
    }
    const result = await buildContextExport({ sessionId: 's', view, request: req({ seq: 10 }), data, read, version: 'v' })
    assert.equal(result.coverage.approximate, true)
    assert.equal(result.coverage.missingLiveNodes, 0)
    assert.equal(result.coverage.requestHeaderAvailable, false)
    assert.equal(result.coverage.systemNodeAvailable, false)
    assert.deepEqual(result.coverage.missingEvents.map(x => x.reason), ['not_found', 'permission denied', 'sequence_mismatch'])
    assert.deepEqual(result.nodes.map(x => x.event?.seq ?? null), [null, null, null, 4])
    assert.equal(result.coverage.allListedEventsFetched, false)
  })

  test('live mode remains explicitly unsent and includes coverage gaps and non-Error failures', async () => {
    const data = tl({ nodes: [surfaceNodeAt(1)], droppedNodes: 2, events: [{ kind: 'prune', seq: 50, time: 1 }] })
    const view = assemble(data, null, null)
    const result = await buildContextExport({ sessionId: 's', view, request: null, data,
      read: async () => { throw 'offline' }, version: 'v' })
    assert.deepEqual(result.selection, { mode: 'live_next_request' })
    assert.equal(result.coverage.missingLiveNodes, 2)
    assert.equal(result.coverage.missingEvents[0].reason, 'offline')
    assert.equal(result.contextEvents.length, 1)
    assert.equal(result.tokenStatistics.providerReportedPromptTokens, null)
  })
})

describe('raw history reader', () => {
  test('uses a seq-bound request, preserves raw events, and reuses events read on the same page', async () => {
    const page = vi.fn(async () => ({ ok: true, value: { records: [
      { type: 'chunks', chunks: [] }, { event: event(3) }, { event: event(5, 'developer/message') },
      { event: { seq: 8, type: 'bad', data: null } }, null, 7,
    ] } }))
    const read = makeExportReader(page, 'session-test')
    assert.deepEqual(await read(5), event(5, 'developer/message'))
    assert.deepEqual(await read(3), event(3))
    assert.equal(page.mock.calls.length, 1)
    assert.equal(await read(8), null)
    assert.equal((page.mock.calls[0] as unknown as [{ throughSeq: number }])[0].throughSeq, 5)
  })

  test('accepts bare history payloads and rejects failed or malformed responses', async () => {
    assert.deepEqual(await makeExportReader(async () => ({ records: [event(1)] }), 's')(1), event(1))
    for (const response of [null, 2, { ok: false }, { ok: true, value: null }, { records: 'not rows' }]) {
      await assert.rejects(makeExportReader(async () => response, 's')(1), /history read failed/)
    }
  })
})

test('download serializes UTF-8 JSON to a safe filename and releases its URL', () => {
  vi.useFakeTimers()
  const create = vi.fn(() => 'blob:local-export')
  const revoke = vi.fn()
  vi.stubGlobal('URL', class { static createObjectURL = create; static revokeObjectURL = revoke })
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  assert.equal(exportFilename('a/../b', null), 'dsh-context-a____b-live.json')
  assert.match(exportFilename('s', req({ seq: 10, turn: 3, step: 1 })), /T3-S1-seq10/)
  assert.match(exportFilename('s', req({ turn: undefined, step: undefined })), /Tunknown-Sunknown/)
  downloadJson('file.json', { 地点: '图书馆' })
  assert.equal(click.mock.calls.length, 1)
  assert.equal(create.mock.calls.length, 1)
  assert.equal(document.querySelector('a[download="file.json"]'), null)
  vi.runAllTimers()
  assert.equal(revoke.mock.calls[0][0], 'blob:local-export')
  vi.useRealTimers()
})
