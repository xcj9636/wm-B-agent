import { onBeforeUnmount, ref } from 'vue'
import { videoApi } from '@/api/video'
import type {
  MediaGenerationEvent,
  MediaGenerationJob,
  MediaGenerationJobStatus,
  MediaGenerationStreamEvent,
} from '@/types/video'

interface PendingMediaJob {
  jobId?: string
  projectId: string
  storyboardVersionId: string
  shotId: string
  idempotencyKey: string
  lastEventId: number
}

type LiveState = 'idle' | 'connecting' | 'live' | 'paused' | 'complete'

const pendingMediaJobKey = 'b-agent:pending-media-generation-job'
export const TERMINAL_MEDIA_JOB_STATUSES = new Set<MediaGenerationJobStatus>([
  'succeeded',
  'failed',
  'cancelled',
])

function readPendingMediaJob(): PendingMediaJob | null {
  try {
    const raw = sessionStorage.getItem(pendingMediaJobKey)
    if (!raw) return null
    const value = JSON.parse(raw) as PendingMediaJob
    if (
      !value.projectId
      || !value.storyboardVersionId
      || !value.shotId
      || !value.idempotencyKey
      || !Number.isSafeInteger(value.lastEventId)
      || value.lastEventId < 0
    ) {
      throw new Error('Invalid pending media job')
    }
    return value
  } catch {
    sessionStorage.removeItem(pendingMediaJobKey)
    return null
  }
}

function writePendingMediaJob(pending: PendingMediaJob) {
  sessionStorage.setItem(pendingMediaJobKey, JSON.stringify(pending))
}

function makeIdempotencyKey(shotId: string) {
  return `video:generation:${shotId}:${crypto.randomUUID()}`
}

function waitForReconnect(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, 1200)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

export function useMediaJobTimeline() {
  const mediaJob = ref<MediaGenerationJob | null>(null)
  const mediaJobEvents = ref<MediaGenerationEvent[]>([])
  const liveState = ref<LiveState>('idle')
  const streamError = ref<string | null>(null)
  let pending: PendingMediaJob | null = null
  let streamController: AbortController | null = null

  function appendStreamEvent(event: MediaGenerationStreamEvent) {
    if (typeof event.id !== 'number' || event.event === 'heartbeat' || !pending) return
    if (mediaJobEvents.value.some((item) => item.sequence === event.id)) return
    const { created_at: createdAt, ...safeData } = event.data
    mediaJobEvents.value.push({
      sequence: event.id,
      event_type: event.event,
      data: safeData,
      created_at: typeof createdAt === 'string' ? createdAt : new Date().toISOString(),
    })
    mediaJobEvents.value.sort((left, right) => left.sequence - right.sequence)
    pending.lastEventId = Math.max(pending.lastEventId, event.id)
    writePendingMediaJob(pending)
  }

  async function consumeEvents() {
    if (!pending?.jobId) return
    streamController?.abort()
    streamController = new AbortController()
    const signal = streamController.signal
    liveState.value = 'connecting'
    streamError.value = null
    try {
      while (!signal.aborted && pending?.jobId) {
        liveState.value = 'live'
        const replay = await videoApi.streamGenerationJobEvents(
          pending.jobId,
          pending.lastEventId,
          appendStreamEvent,
          signal,
        )
        mediaJob.value = await videoApi.getGenerationJob(pending.jobId)
        if (
          TERMINAL_MEDIA_JOB_STATUSES.has(mediaJob.value.status)
          || TERMINAL_MEDIA_JOB_STATUSES.has(replay.jobStatus as MediaGenerationJobStatus)
        ) {
          liveState.value = 'complete'
          sessionStorage.removeItem(pendingMediaJobKey)
          return
        }
        await waitForReconnect(signal)
      }
    } catch (error) {
      if (signal.aborted) return
      liveState.value = 'paused'
      streamError.value = error instanceof Error ? error.message : 'Media stream failed'
    }
  }

  async function hydrate(pendingJob: PendingMediaJob) {
    if (!pendingJob.jobId) return
    mediaJob.value = await videoApi.getGenerationJob(pendingJob.jobId)
    const eventPage = await videoApi.listGenerationJobEvents(pendingJob.jobId)
    mediaJobEvents.value = eventPage.items
    pendingJob.lastEventId = eventPage.next_sequence
    writePendingMediaJob(pendingJob)
  }

  async function startMediaJob(
    projectId: string,
    storyboardVersionId: string,
    shotId: string,
  ) {
    const existing = readPendingMediaJob()
    pending = existing
      && existing.projectId === projectId
      && existing.storyboardVersionId === storyboardVersionId
      && existing.shotId === shotId
      ? existing
      : {
          projectId,
          storyboardVersionId,
          shotId,
          idempotencyKey: makeIdempotencyKey(shotId),
          lastEventId: 0,
        }
    writePendingMediaJob(pending)
    const created = await videoApi.createGenerationJob(
      projectId,
      storyboardVersionId,
      shotId,
      pending.idempotencyKey,
    )
    pending.jobId = created.id
    mediaJob.value = created
    writePendingMediaJob(pending)
    await hydrate(pending)
    void consumeEvents()
    return created
  }

  async function restoreMediaJob() {
    pending = readPendingMediaJob()
    if (!pending) return null
    try {
      if (!pending.jobId) {
        const restored = await videoApi.createGenerationJob(
          pending.projectId,
          pending.storyboardVersionId,
          pending.shotId,
          pending.idempotencyKey,
        )
        pending.jobId = restored.id
        writePendingMediaJob(pending)
      }
      await hydrate(pending)
      if (mediaJob.value && TERMINAL_MEDIA_JOB_STATUSES.has(mediaJob.value.status)) {
        liveState.value = 'complete'
        sessionStorage.removeItem(pendingMediaJobKey)
      } else {
        void consumeEvents()
      }
      return mediaJob.value
    } catch (error) {
      liveState.value = 'paused'
      streamError.value = error instanceof Error ? error.message : 'Media job restore failed'
      return null
    }
  }

  function resumeMediaJob() {
    if (pending?.jobId) void consumeEvents()
  }

  function stopMediaJobStream() {
    streamController?.abort()
    streamController = null
  }

  onBeforeUnmount(stopMediaJobStream)

  return {
    mediaJob,
    mediaJobEvents,
    liveState,
    streamError,
    startMediaJob,
    restoreMediaJob,
    resumeMediaJob,
    stopMediaJobStream,
  }
}
