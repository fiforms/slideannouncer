// The order of the first-run wizard's steps, including any a product adds
// through `setupSteps` (product.js; docs/PRODUCTS.md). Core steps ask this for
// where Back/Next go instead of naming each other's paths, so a product step
// can sit between two of them.
//
// A product step is `{ key, after, path, component }`: `key` names it (the
// progress bar shows `setup.steps.<key>` from the product's `messages`),
// `after` is the core step key it follows ('welcome' | 'network' | 'name' |
// 'pairing'), `path` is relative to /setup. Several steps after the same key
// keep the order they were given in.
import { product } from './product.js'

const CORE = ['welcome', 'network', 'name', 'pairing', 'done']

// Read lazily, never at module load: a product's own wizard steps import this file
// (for nextPath/prevPath), and the product definition imports those steps, so
// `product` is not yet defined when this module first runs — a top-level read
// threw "Cannot access 'product' before initialization" and left a blank screen.
export const getProductSteps = () => product.setupSteps || []

export const getStepKeys = () =>
  CORE.flatMap((key) => [
    key,
    ...getProductSteps().filter((step) => step.after === key).map((step) => step.key),
  ])

export function stepPath(key) {
  const own = getProductSteps().find((step) => step.key === key)
  if (own) return `/setup/${own.path}`
  return key === 'welcome' ? '/setup' : `/setup/${key}`
}

export function nextPath(key) {
  const keys = getStepKeys()
  return stepPath(keys[keys.indexOf(key) + 1])
}

export function prevPath(key) {
  const keys = getStepKeys()
  return stepPath(keys[keys.indexOf(key) - 1])
}
