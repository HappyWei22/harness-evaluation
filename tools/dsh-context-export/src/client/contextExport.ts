// Local context-export extension; modified from upstream. See MODIFICATIONS.md.
import type { Assembled } from './assemble'
import type { ContextTimeline, RequestRecord } from '../shared/types'
import type { SessionPageFace } from './services'

export interface LogEvent {
  seq: number
  type: string
  data: Record<string, unknown>
  [key: string]: unknown
}

export type EventReader = (seq: number) => Promise<LogEvent | null>

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? value as Record<string, unknown> : null
}

export function makeExportReader(page: SessionPageFace['page'], sessionId: string): EventReader {
  const cache = new Map<number, LogEvent>()
  return async (seq) => {
    const cached = cache.get(seq)
    if (cached !== undefined) return cached
    const response = record(await page({
      address: { kind: 'session', sessionId }, throughSeq: seq, beforeSeq: seq + 1,
    }, new AbortController().signal))
    const payload = response !== null && 'ok' in response
      ? response.ok === true ? record(response.value) : null
      : response
    if (payload === null || !Array.isArray(payload.records)) throw new Error('Session history read failed')
    for (const entry of payload.records) {
      const row = record(entry)
      const event = record(row?.event ?? row)
      if (event === null || typeof event.seq !== 'number' || typeof event.type !== 'string' || record(event.data) === null) continue
      cache.set(event.seq, event as LogEvent)
    }
    return cache.get(seq) ?? null
  }
}

export async function buildContextExport(input: {
  sessionId: string
  view: Assembled
  request: RequestRecord | null
  data: ContextTimeline
  read: EventReader
  version: string
  exportedAt?: string
}) {
  const { view, request, read, data } = input
  const seqs = new Set(view.nodes.map(node => node.seq))
  if (view.system !== null) seqs.add(view.system.seq)
  if (view.header !== null) seqs.add(view.header.seq)
  const events = new Map<number, LogEvent>()
  const missing: Array<{ seq: number; reason: string }> = []
  // Serial reads bound memory and retain the exact click-time selection while the view keeps updating.
  for (const seq of [...seqs].sort((a, b) => a - b)) {
    try {
      const event = await read(seq)
      if (event === null) missing.push({ seq, reason: 'not_found' })
      else if (event.seq !== seq) missing.push({ seq, reason: 'sequence_mismatch' })
      else events.set(seq, event)
    } catch (error) {
      missing.push({ seq, reason: error instanceof Error ? error.message : String(error) })
    }
  }
  const eventAt = (seq: number | undefined) => seq === undefined ? null : events.get(seq) ?? null
  return {
    schema: 'dsh-context.selected-context',
    schemaVersion: 1,
    exportedAt: input.exportedAt ?? new Date().toISOString(),
    exporter: { name: 'dsh-context', version: input.version, extension: 'context-export-1' },
    sessionId: input.sessionId,
    evidence: {
      kind: 'log_reconstructed_context',
      actualProviderRequestCaptured: false,
      note: 'This is the plugin-selected reconstruction, not a captured provider request or a complete session log.',
    },
    selection: request === null
      ? { mode: 'live_next_request' as const }
      : {
        mode: 'historical_request' as const,
        turn: request.turn ?? null,
        step: request.step ?? null,
        responseSeq: request.seq,
        time: request.time,
      },
    coverage: {
      approximate: view.approximate,
      missingLiveNodes: view.missingLive,
      requestHeaderAvailable: view.header !== null,
      systemNodeAvailable: view.system !== null,
      allListedEventsFetched: missing.length === 0,
      missingEvents: missing,
      attachments: 'references_only; download the native session ZIP for attachment bytes',
      order: 'plugin assembly order; provider-specific serialization is not reproduced',
    },
    tokenStatistics: {
      estimated: request ?? data.current,
      providerReportedPromptTokens: request?.prompt ?? null,
    },
    header: { metadata: view.header, event: eventAt(view.header?.seq) },
    system: { metadata: view.system, event: eventAt(view.system?.seq) },
    nodes: view.nodes.map(node => ({ metadata: node, event: eventAt(node.seq) })),
    contextEvents: data.events.filter(event => request === null || event.seq < request.seq),
  }
}

export function exportFilename(sessionId: string, request: RequestRecord | null): string {
  const id = sessionId.replace(/[^A-Za-z0-9_-]/g, '_')
  const step = request === null ? 'live' : `T${request.turn ?? 'unknown'}-S${request.step ?? 'unknown'}-seq${request.seq}`
  return `dsh-context-${id}-${step}.json`
}

export function downloadJson(filename: string, value: unknown): void {
  const blob = new Blob([JSON.stringify(value, null, 2) + '\n'], { type: 'application/json;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  try {
    anchor.click()
  } finally {
    anchor.remove()
    window.setTimeout(() => { URL.revokeObjectURL(url) }, 30_000)
  }
}
