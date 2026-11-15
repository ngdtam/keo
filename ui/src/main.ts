import './assets/css/main.css'
import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ui from '@nuxt/ui/vue-plugin'
import App from './App.vue'
import ActivityPage from './pages/ActivityPage.vue'
import BrowsePage from './pages/BrowsePage.vue'
import HomePage from './pages/HomePage.vue'
import LibraryPage from './pages/LibraryPage.vue'
import LoginPage from './pages/LoginPage.vue'
import OrganizePreviewPage from './pages/OrganizePreviewPage.vue'
import ReviewPage from './pages/ReviewPage.vue'
import SearchPage from './pages/SearchPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import SongDetailPage from './pages/SongDetailPage.vue'
import TrashPage from './pages/TrashPage.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomePage },
    { path: '/login', component: LoginPage },
    { path: '/library', component: LibraryPage },
    { path: '/library/organize/preview', component: OrganizePreviewPage },
    { path: '/library/trash', component: TrashPage },
    { path: '/library/:kind/:ident', component: SongDetailPage },
    { path: '/search', component: SearchPage },
    { path: '/charts', component: BrowsePage },
    { path: '/moods', component: BrowsePage },
    { path: '/moods/:params', component: BrowsePage },
    { path: '/playlists/:id', component: BrowsePage },
    { path: '/songs/:videoId/radio', component: BrowsePage },
    { path: '/review', component: ReviewPage },
    { path: '/review/:videoId/pick', component: ReviewPage },
    { path: '/activity', component: ActivityPage },
    { path: '/settings', component: SettingsPage },
  ],
})

const app = createApp(App)
app.use(router)
app.use(ui)
app.mount('#app')
