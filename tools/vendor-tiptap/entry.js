// Kern-Bundle static/vendor/tiptap/saga-pm.js -> window.SagaPM (Exportvertrag: static/vendor/tiptap/README)
// Einzige Kopie von Tiptap Core und ProseMirror im Browser. saga-y.js bringt keine eigene mit, sondern
// greift ueber window.SagaPM auf genau diese Module zu (build.mjs, Plugin "sagapm-global").
import * as core from '@tiptap/core'
import * as model from '@tiptap/pm/model'
import * as state from '@tiptap/pm/state'
import * as view from '@tiptap/pm/view'
import * as transform from '@tiptap/pm/transform'
import * as commands from '@tiptap/pm/commands'
import * as keymap from '@tiptap/pm/keymap'
import * as history from '@tiptap/pm/history'
import * as inputrules from '@tiptap/pm/inputrules'
import * as schemaList from '@tiptap/pm/schema-list'
import * as dropcursor from '@tiptap/pm/dropcursor'
import * as gapcursor from '@tiptap/pm/gapcursor'
import { StarterKit } from '@tiptap/starter-kit'
import { Placeholder, TrailingNode, Dropcursor, Gapcursor, UndoRedo, Focus, Selection, CharacterCount } from '@tiptap/extensions'
import { Suggestion, SuggestionPluginKey, exitSuggestion, findSuggestionMatch } from '@tiptap/suggestion'
import { DragHandle, DragHandlePlugin, dragHandlePluginDefaultKey, defaultComputePositionConfig, defaultRules, normalizeNestedOptions } from '@tiptap/extension-drag-handle'
import { NodeRange, NodeRangeSelection, getNodeRangeDecorations, getSelectionRanges, isNodeRangeSelection } from '@tiptap/extension-node-range'
import { UniqueID, generateUniqueIds } from '@tiptap/extension-unique-id'
import { BubbleMenu, BubbleMenuPlugin, BubbleMenuView } from '@tiptap/extension-bubble-menu'
import * as floating from '@floating-ui/dom'

export const version = __SAGA_TIPTAP_VERSION__
export { core, StarterKit, Placeholder, TrailingNode, Dropcursor, Gapcursor, UndoRedo, Focus, Selection, CharacterCount,
  Suggestion, SuggestionPluginKey, exitSuggestion, findSuggestionMatch,
  DragHandle, DragHandlePlugin, dragHandlePluginDefaultKey, defaultComputePositionConfig, defaultRules, normalizeNestedOptions,
  NodeRange, NodeRangeSelection, getNodeRangeDecorations, getSelectionRanges, isNodeRangeSelection,
  UniqueID, generateUniqueIds, BubbleMenu, BubbleMenuPlugin, BubbleMenuView, floating }
export const pm = { model, state, view, transform, commands, keymap, history, inputrules, schemaList, dropcursor, gapcursor }
