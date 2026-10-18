// The product this frontend is built for — what the build's `@product` alias
// (vite.config.js) points at, with defaults filled in so a product only
// supplies what it has. See docs/PRODUCTS.md, "Frontend seams", for the shape.
import definition from '@product'
import { addMessages } from './i18n.js'

export const product = {
  name: '',
  mainView: null,
  overlay: null,
  settingsRoutes: [],
  // Extra first-run wizard steps — see setupFlow.js.
  setupSteps: [],
  railCategories: () => [],
  advancedSection: null,
  loadSettings: async () => {},
  settingsPin: async () => null,
  ...definition,
}

// The product's strings (`messages`) join the core ones in vue-i18n.
addMessages(definition.messages)
