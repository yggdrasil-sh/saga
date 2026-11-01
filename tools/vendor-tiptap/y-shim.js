// Ersatz fuer @tiptap/y-tiptap und @tiptap/extension-collaboration IM KERN-BUNDLE.
// @tiptap/extension-drag-handle importiert beide nur, um bei Live-Bearbeitung Positionen relativ zu halten.
// Ohne Ersatz kaeme yjs ein zweites Mal (in saga-pm.js) in den Browser. Der Shim reicht zur Laufzeit an
// window.SagaY weiter; ist saga-y.js nicht geladen, verhaelt sich DragHandle wie ohne Collaboration.
const Y = () => (typeof window !== 'undefined' && window.SagaY && window.SagaY.ytiptap) || null
export const ySyncPluginKey = {
  get key () { const y = Y(); return y ? y.ySyncPluginKey.key : 'y-sync$' },
  getState (state) { const y = Y(); return y ? y.ySyncPluginKey.getState(state) : undefined }
}
export function absolutePositionToRelativePosition (...a) { const y = Y(); return y ? y.absolutePositionToRelativePosition(...a) : null }
export function relativePositionToAbsolutePosition (...a) { const y = Y(); return y ? y.relativePositionToAbsolutePosition(...a) : null }
export function isChangeOrigin (tr) { return !!(Y() && tr.getMeta(ySyncPluginKey)) }
