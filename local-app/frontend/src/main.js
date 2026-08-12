import { createApp } from 'vue'
import App from './App.vue'
import router from './router.js'
import { installRemoteNav } from './remoteNav.js'
import './style.css'

installRemoteNav(router)
createApp(App).use(router).mount('#app')
