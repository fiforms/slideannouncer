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

export const productSteps = product.setupSteps || []

export const stepKeys = CORE.flatMap((key) => [
  key,
  ...productSteps.filter((step) => step.after === key).map((step) => step.key),
])

export function stepPath(key) {
  const own = productSteps.find((step) => step.key === key)
  if (own) return `/setup/${own.path}`
  return key === 'welcome' ? '/setup' : `/setup/${key}`
}

export function nextPath(key) {
  return stepPath(stepKeys[stepKeys.indexOf(key) + 1])
}

export function prevPath(key) {
  return stepPath(stepKeys[stepKeys.indexOf(key) - 1])
}
