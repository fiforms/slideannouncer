import { createRouter, createWebHistory } from 'vue-router'
import Home from './views/Home.vue'
import SettingsLayout from './views/settings/SettingsLayout.vue'
import NetworkStatus from './views/settings/NetworkStatus.vue'
import WifiList from './views/settings/WifiList.vue'
import WifiConnect from './views/settings/WifiConnect.vue'
import System from './views/settings/System.vue'
import About from './views/settings/About.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home },
    {
      path: '/settings',
      component: SettingsLayout,
      children: [
        { path: '', redirect: '/settings/network' },
        { path: 'network', component: NetworkStatus },
        { path: 'network/wifi', component: WifiList },
        { path: 'network/wifi/:ssid', component: WifiConnect, props: true },
        { path: 'system', component: System },
        { path: 'about', component: About },
      ],
    },
  ],
})
