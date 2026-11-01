// Live-Bundle static/vendor/tiptap/saga-y.js -> window.SagaY. Setzt saga-pm.js voraus (vorher laden).
// Tiptap Core und alle ProseMirror-Module kommen aus window.SagaPM (keine zweite Kopie, build.mjs).
// yjs existiert nur hier - Saga-Code nimmt Y ausschliesslich aus SagaY.Y.
import * as Y from 'yjs'
import * as awareness from 'y-protocols/awareness'
import * as sync from 'y-protocols/sync'
import * as ytiptap from '@tiptap/y-tiptap'
import { Collaboration, isChangeOrigin, getUpdatedPosition, createMappablePosition, CollaborationMappablePosition } from '@tiptap/extension-collaboration'
import { CollaborationCaret } from '@tiptap/extension-collaboration-caret'

export const version = __SAGA_TIPTAP_VERSION__
export { Y, awareness, sync, ytiptap, Collaboration, CollaborationCaret, isChangeOrigin, getUpdatedPosition, createMappablePosition, CollaborationMappablePosition }
