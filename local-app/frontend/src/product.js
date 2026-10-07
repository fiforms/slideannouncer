// The product this frontend is built for — what the build's `@product` alias
// (vite.config.js) points at, with defaults filled in so a product only
// supplies what it has. See docs/PRODUCTS.md, "Frontend seams", for the shape.
import definition from '@product'

export const product = {
  name: '',
  mainView: null,
  overlay: null,
  settingsRoutes: [],
  railCategories: () => [],
  advancedSection: null,
  loadSettings: async () => {},
  settingsPin: async () => null,
  ...definition,
}
