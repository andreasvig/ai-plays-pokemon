// Recording mode — read once from the URL at load.
//
// The MP4 recorder (src/dashboard/recorder.py) drives its OWN headless Chrome at
//   /spectate?record=1&view=simple|detailed&run=<run_id>
// so that the video is independent of what the human viewer is doing: their
// route, their focus, whether the window is minimised, whether the app is open
// at all. These params are the whole contract between the two.
//
// `run` PINS the run id instead of letting the page read it from
// /api/emulator/status. That field goes null the instant the run ends, which
// would drop the recorder out of the view for the final seconds of every video
// — the frames that matter most.
//
// Query params, not a route: /spectate stays one URL, and a human who lands on
// a recorder link by accident just sees a normal (pinned) spectate.

const params =
  typeof location !== 'undefined'
    ? new URLSearchParams(location.search)
    : new URLSearchParams()

/** True when this page IS a recording surface (never for a human's tab). */
export const recording = params.get('record') === '1'

/** 'simple' | 'detailed' | null — which presentation to pin. */
export const recordView = ['simple', 'detailed'].includes(params.get('view'))
  ? params.get('view')
  : null

/** Run id to pin, or null to track the live active run as usual. */
export const recordRun = params.get('run') || null

/**
 * What `simple` should be forced to, or null to leave it to the user's
 * localStorage preference. Only ever non-null on a recorder page.
 */
export const forcedSimple = recording && recordView ? recordView === 'simple' : null
