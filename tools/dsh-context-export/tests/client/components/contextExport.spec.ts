// Local context-export extension; modified from upstream. See MODIFICATIONS.md.
import { act, createElement as h } from 'react'
import assert from 'node:assert/strict'
import { afterEach, describe, test, vi } from 'vitest'
import { ContextExport } from '../../../src/client/components/contextExport'
import { assemble } from '../../../src/client/assemble'
import { watchHistoryFaces } from '../../../src/client/historyPage'
import { watchNativeLogExport } from '../../../src/client/nativeLogExport'
import * as exported from '../../../src/client/contextExport'
import { asClientCtx, TestClientCtx } from '../helpers/harness'
import { click, makeKit, mount, queryAll, surfaceNodeAt, type Mounted } from '../helpers/kit'
import { req, tl } from './browserHarness'

const contexts: TestClientCtx[] = []
const mounts: Mounted[] = []
afterEach(async () => {
  for (const mounted of mounts.splice(0)) await mounted.unmount()
  for (const context of contexts.splice(0)) context.dispose()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function arm(page?: unknown, native?: unknown): TestClientCtx {
  const context = new TestClientCtx({ services: {
    remote: { session: { page } }, 'remote.session': { page },
    ...(native !== undefined ? { sessionLogDownload: native } : {}),
  } })
  contexts.push(context)
  watchHistoryFaces(asClientCtx(context))
  watchNativeLogExport(asClientCtx(context))
  return context
}

async function render(sessionId?: string, partial = false, disabled = false): Promise<Mounted> {
  const data = tl({ nodes: [surfaceNodeAt(2)], ...(partial ? { archiveFloor: 99 } : {}) })
  const request = req({ seq: 10, turn: 3, step: 1 })
  const view = assemble(data, { headers: [{ seq: 1, time: 1, tools: [] }] }, request.seq)
  const mounted = await mount(h(ContextExport, { kit: makeKit('zh'), sessionId, data, request, view, disabled }))
  mounts.push(mounted)
  return mounted
}

describe('context export controls', () => {
  test('downloads the selected context with full raw events and the current session id', async () => {
    vi.stubGlobal('__DSH_CTX_VERSION__', '0.72.0')
    arm(async (request: { throughSeq: number }) => ({ ok: true, value: { records: [{ event: {
      seq: request.throughSeq, type: 'user/message', data: { content: [{ type: 'text', text: '完整正文4500' }] },
    } }] } }))
    const save = vi.spyOn(exported, 'downloadJson').mockImplementation(() => {})
    const mounted = await render('session-ctx')
    await click(queryAll(mounted.container, 'button')[0])
    assert.equal(save.mock.calls.length, 1)
    assert.match(save.mock.calls[0][0], /session-ctx-T3-S1/)
    const value = save.mock.calls[0][1] as { sessionId: string; nodes: { event: unknown }[] }
    assert.equal(value.sessionId, 'session-ctx')
    assert.match(JSON.stringify(value.nodes[0].event), /完整正文4500/)
    assert.match(mounted.container.textContent ?? '', /已交给下载器/)
  })

  test('partial exports are labelled, and native log export calls the real service face with correct this', async () => {
    vi.stubGlobal('__DSH_CTX_VERSION__', '0.72.0')
    const native = { ids: [] as string[], async download(id: string) { this.ids.push(id) } }
    await act(async () => { arm(async () => ({ records: [] }), native) })
    vi.spyOn(exported, 'downloadJson').mockImplementation(() => {})
    const mounted = await render('session-partial', true)
    await click(queryAll(mounted.container, 'button')[0])
    assert.match(mounted.container.textContent ?? '', /带缺口/)
    await click(queryAll(mounted.container, 'button')[1])
    assert.deepEqual(native.ids, ['session-partial'])
    assert.match(mounted.container.textContent ?? '', /已调用 DSH 原生导出/)
  })

  test('missing capabilities and missing session disable buttons; failed details disable only context export', async () => {
    arm(undefined)
    const empty = await render()
    assert.ok(queryAll<HTMLButtonElement>(empty.container, 'button').every(button => button.disabled))
    const native = { download: vi.fn(async () => {}) }
    await act(async () => { arm(async () => ({ records: [] }), native) })
    const loading = await render('s', false, true)
    const buttons = queryAll<HTMLButtonElement>(loading.container, 'button')
    assert.equal(buttons[0].disabled, true)
    assert.equal(buttons[1].disabled, false)
  })

  test('prevents duplicate export while content reads are pending', async () => {
    vi.stubGlobal('__DSH_CTX_VERSION__', '0.72.0')
    let finish!: (value: unknown) => void
    let first = true
    arm(async () => {
      if (first) { first = false; return new Promise(resolve => { finish = resolve }) }
      return { records: [] }
    })
    const save = vi.spyOn(exported, 'downloadJson').mockImplementation(() => {})
    const mounted = await render('s')
    const button = queryAll<HTMLButtonElement>(mounted.container, 'button')[0]
    await click(button)
    assert.equal(button.disabled, true)
    await click(button)
    await act(async () => { finish({ records: [] }) })
    assert.equal(save.mock.calls.length, 1)
  })

  test('reports download and native-service errors without claiming success', async () => {
    vi.stubGlobal('__DSH_CTX_VERSION__', '0.72.0')
    arm(async () => ({ records: [] }), { download: async () => { throw 'native unavailable' } })
    vi.spyOn(exported, 'downloadJson').mockImplementation(() => { throw new Error('save refused') })
    const mounted = await render('s')
    await click(queryAll(mounted.container, 'button')[0])
    assert.match(mounted.container.textContent ?? '', /导出失败：save refused/)
    await click(queryAll(mounted.container, 'button')[1])
    assert.match(mounted.container.textContent ?? '', /导出失败：native unavailable/)
  })

  test('hostile native-export services do not break the view', async () => {
    arm(undefined, null)
    arm(undefined, { download: 7 })
    arm(undefined, Object.defineProperty({}, 'download', { get() { throw new Error('bad face') } }))
    const mounted = await render('s')
    assert.equal(queryAll<HTMLButtonElement>(mounted.container, 'button')[1].disabled, true)
  })
})
