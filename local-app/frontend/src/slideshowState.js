// Shared multi-show state, polled once by Slideshow.vue and read (plus
// updated optimistically on pin) by MenuOverlay.vue — so the overlay can
// show the current show list and pin instantly, without a duplicate fetch
// or waiting up to REFRESH_INTERVAL_MS for the next poll.
import { ref } from 'vue'
import { api } from './api.js'

export const shows = ref([])
export const settings = ref({})
export const pinnedShowId = ref(null)

export async function refreshShows() {
  const data = await api.slideshow()
  shows.value = data.shows || []
  settings.value = data.settings || {}
  pinnedShowId.value = data.pinned_show_id ?? null
  return data
}

// pinned_show_id from the backend is already resolved (pin, else Main,
// else the first show) — this mirrors that same fallback chain client-side
// so the display never blanks between a pin going stale and the next poll
// correcting it.
export function activeShow() {
  return (
    shows.value.find((show) => show.id === pinnedShowId.value) ||
    shows.value.find((show) => show.is_main) ||
    shows.value[0] ||
    null
  )
}

export async function pinShow(showId) {
  await api.pinShow(showId)
  pinnedShowId.value = showId
}
