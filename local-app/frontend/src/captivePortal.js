// "Sign in to this network" for captive-portal WiFi (hotel/guest login
// pages). The kiosk is a single Chromium tab with nowhere else to show a
// web page, so this sends the whole tab to the portal; the backend
// (captive_portal.py) watches connectivity meanwhile and navigates the tab
// back to `returnPath` (Settings > Network, or the setup wizard's Network
// step) once online — or the remote's Back key returns through normal
// browser history.
import { api } from './api.js'
import { grantReloadPass } from './pinLock.js'

export async function openCaptivePortal(returnPath = '/settings/network') {
  const { url } = await api.networkPortalSignIn(returnPath)
  grantReloadPass()
  window.location.href = url
}
