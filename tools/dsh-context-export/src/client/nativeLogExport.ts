// Local context-export extension; modified from upstream. See MODIFICATIONS.md.
import { useSyncExternalStore } from 'react'
import type { ClientCtx } from './services'

export type NativeLogExporter = (sessionId: string) => Promise<void>

let exporter: NativeLogExporter | undefined
const listeners = new Set<() => void>()

function notify(value: NativeLogExporter | undefined): void {
  exporter = value
  for (const listener of [...listeners]) listener()
}

export function watchNativeLogExport(ctx: ClientCtx): void {
  ctx.inject(['sessionLogDownload'], (scope) => {
    try {
      const face = scope.get('sessionLogDownload') as { download?: unknown } | null | undefined
      if (face === undefined || face === null || typeof face.download !== 'function') return
      const download = face.download as NativeLogExporter
      notify(id => download.call(face, id))
    } catch {
      return
    }
    return () => { notify(undefined) }
  })
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => { listeners.delete(listener) }
}

export function useNativeLogExporter(): NativeLogExporter | undefined {
  return useSyncExternalStore(subscribe, () => exporter, () => exporter)
}
