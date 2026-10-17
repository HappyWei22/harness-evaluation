// Local context-export extension; modified from upstream. See MODIFICATIONS.md.
import { useRef, useState, type ReactElement } from 'react'
import type { Assembled } from '../assemble'
import type { ContextTimeline, RequestRecord } from '../../shared/types'
import { buildContextExport, downloadJson, exportFilename, makeExportReader } from '../contextExport'
import { useHistoryFace } from '../historyPage'
import { useNativeLogExporter } from '../nativeLogExport'
import type { ViewKit } from '../viewkit'

declare const __DSH_CTX_VERSION__: string

export function ContextExport(props: {
  kit: ViewKit
  sessionId?: string
  view: Assembled
  request: RequestRecord | null
  data: ContextTimeline
  disabled?: boolean
}): ReactElement {
  const { t } = props.kit
  const page = useHistoryFace()
  const nativeExport = useNativeLogExporter()
  const [busy, setBusy] = useState(false)
  const active = useRef(false)
  const [status, setStatus] = useState('')
  const sessionId = props.sessionId
  const validSession = typeof sessionId === 'string' && sessionId !== ''

  const run = async (native: boolean): Promise<void> => {
    if (active.current || !validSession) return
    active.current = true
    setBusy(true)
    setStatus('')
    try {
      if (native) {
        if (nativeExport === undefined) throw new Error(t('export.noNative'))
        await nativeExport(sessionId)
        setStatus(t('export.nativeStarted'))
      } else {
        if (page === undefined) throw new Error(t('export.noHistory'))
        const value = await buildContextExport({
          sessionId,
          view: props.view,
          request: props.request,
          data: props.data,
          read: makeExportReader(page, sessionId),
          version: __DSH_CTX_VERSION__,
        })
        downloadJson(exportFilename(sessionId, props.request), value)
        setStatus(t(value.coverage.allListedEventsFetched && !value.coverage.approximate
          && value.coverage.missingLiveNodes === 0 && value.coverage.requestHeaderAvailable
          ? 'export.started' : 'export.partial'))
      }
    } catch (error) {
      setStatus(t('export.failed') + (error instanceof Error ? error.message : String(error)))
    } finally {
      active.current = false
      setBusy(false)
    }
  }

  return (
    <div className="lc-context-export">
      <div className="lc-context-export-actions">
        <button type="button" className="lc-br-pick" disabled={busy || props.disabled || !validSession || page === undefined}
          title={t('export.contextTip')} onClick={() => { void run(false) }}>{t(busy ? 'export.busy' : 'export.context')}</button>
        <button type="button" className="lc-br-pick" disabled={busy || !validSession || nativeExport === undefined}
          title={t('export.logTip')} onClick={() => { void run(true) }}>{t('export.log')}</button>
      </div>
      <div className="lc-context-export-note">{t('export.scope')}</div>
      {status !== '' ? <div className="lc-context-export-note" role="status">{status}</div> : null}
    </div>
  )
}
