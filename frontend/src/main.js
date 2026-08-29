import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import CustomerConsultation from './views/CustomerConsultation.vue'
import StaffConsole from './views/StaffConsole.vue'
import './styles.css'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'customer', component: CustomerConsultation },
    { path: '/staff', name: 'staff', component: StaffConsole },
  ],
})

createApp(App).use(router).mount('#app')