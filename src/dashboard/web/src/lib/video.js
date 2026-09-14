// Where a run's recording streams from. A published row carries the R2 URL of
// its recording (pokemon publish); a local row streams from the control center.
// `variant` "simple" is the 1:1 recording-simple.mp4 a `both` recording writes
// beside the full-panel file — local only, it is never published.
export function recordingUrl(run, variant = 'full') {
  return variant === 'simple'
    ? `/api/runs/${encodeURIComponent(run.runId)}/recording-simple.mp4`
    : (run.videoUrl ?? `/api/runs/${encodeURIComponent(run.runId)}/recording.mp4`)
}
