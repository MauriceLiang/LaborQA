<!-- 文件作用：分页展示文档包含的文本块并支持查看详情。 -->
<script setup lang="ts">
import { ElPagination } from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'

import type { ChunkItem } from '@/api/documents'

defineProps<{
  /** 当前页显示的文本块。 */
  chunks: ChunkItem[]
  /** 列表或分页请求是否正在进行。 */
  loading: boolean
  /** 当前页码。 */
  page: number
  /** 每页文本块数量。 */
  size: number
  /** 文本块总数。 */
  total: number
  /** 当前选中文本块的标识。 */
  selectedId?: number
}>()

// 向父组件回报文本块选择及分页器操作。
const emit = defineEmits<{
  select: [chunk: ChunkItem]
  page: [page: number]
  size: [size: number]
}>()

/** 折叠空白并截断较长文本块，生成列表预览摘要。
 * @param content 文本块完整正文。
 * @returns 最多 180 个字符的单行摘要。
 */
function summarize(content: string) {
  // compact 是去掉首尾空白并合并内部空白后的文本。
  const compact = content.replace(/\s+/g, ' ').trim()
  return compact.length > 180 ? `${compact.slice(0, 180)}…` : compact
}
</script>

<template>
  <section class="chunk-list" aria-labelledby="chunks-title">
    <div class="section-heading">
      <div>
        <h2 id="chunks-title">文本分块</h2>
        <p>共 {{ total }} 个分块，选择一项查看完整原文。</p>
      </div>
    </div>

    <div v-loading="loading" class="chunk-items">
      <p v-if="!loading && chunks.length === 0" class="empty-copy">该资料尚无可查看的分块。</p>
      <button
        v-for="chunk in chunks"
        :key="chunk.id"
        class="chunk-item"
        :class="{ 'chunk-item-selected': chunk.id === selectedId }"
        type="button"
        @click="emit('select', chunk)"
      >
        <span class="chunk-number">Chunk {{ chunk.chunk_no }}</span>
        <span class="chunk-summary">{{ summarize(chunk.content) }}</span>
        <span class="chunk-action">
          查看原文
          <ArrowRight aria-hidden="true" />
        </span>
      </button>
    </div>

    <ElPagination
      v-if="total > 0"
      class="chunk-pagination"
      background
      layout="prev, pager, next, sizes"
      :current-page="page"
      :page-size="size"
      :page-sizes="[10, 20, 50]"
      :total="total"
      @current-change="(nextPage: number) => emit('page', nextPage)"
      @size-change="(nextSize: number) => emit('size', nextSize)"
    />
  </section>
</template>
