// 文件作用：创建 Vue 应用实例并注册路由、状态管理及全局样式。
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import {
  ElAlert,
  ElButton,
  ElCard,
  ElContainer,
  ElDescriptions,
  ElDescriptionsItem,
  ElEmpty,
  ElHeader,
  ElLoadingDirective,
  ElMain,
  ElSkeleton,
  ElTag,
} from 'element-plus'

import App from './App.vue'
import router from './router'
import 'element-plus/dist/index.css'
import './styles.css'

// 根 Vue 组件对应的应用实例，后续在其上注册插件并挂载页面。
const app = createApp(App)

app.use(createPinia()).use(router)
app.use(ElAlert)
app.use(ElButton)
app.use(ElCard)
app.use(ElContainer)
app.use(ElDescriptions)
app.use(ElDescriptionsItem)
app.use(ElEmpty)
app.use(ElHeader)
app.use(ElMain)
app.use(ElSkeleton)
app.use(ElTag)
app.directive('loading', ElLoadingDirective)
app.mount('#app')
