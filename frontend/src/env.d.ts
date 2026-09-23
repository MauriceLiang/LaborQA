// 文件作用：声明 Vite 客户端环境类型及项目使用的自定义环境变量。
/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
