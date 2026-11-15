import './assets/css/main.css'
import { createApp, defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ui from '@nuxt/ui/vue-plugin'
import App from './App.vue'
import HomePage from './pages/HomePage.vue'
import LoginPage from './pages/LoginPage.vue'

function placeholder(title: string) {
  return defineComponent({
    name: `${title}Placeholder`,
    render: () => h('div', { class: 'p-6 text-sm text-muted' }, `${title} — coming soon.`),
  })
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: HomePage },
    { path: '/login', component: LoginPage },
    { path: '/library', component: placeholder('Library') },
    { path: '/library/:kind/:ident', component: placeholder('LibraryItem') },
    { path: '/search', component: placeholder('Search') },
    { path: '/charts', component: placeholder('Charts') },
    { path: '/moods', component: placeholder('Moods') },
    { path: '/review', component: placeholder('Review') },
    { path: '/activity', component: placeholder('Activity') },
    { path: '/settings', component: placeholder('Settings') },
  ],
})

const app = createApp(App)
app.use(router)
app.use(ui)
app.mount('#app')
