import 'vue-router'

import type { UserRole } from '@/api/auth'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    roles?: UserRole[]
  }
}
