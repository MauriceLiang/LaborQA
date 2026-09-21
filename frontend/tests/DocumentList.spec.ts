import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DocumentList from '@/components/documents/DocumentList.vue'
import type { DocumentItem } from '@/api/documents'

const builtinDocument: DocumentItem = {
  id: 1,
  file_name: '一个很长的系统资料文件名.pdf',
  file_type: 'pdf',
  source_type: 'BUILTIN',
  source_key: 'builtin:劳动法.pdf',
  status: 'SUCCESS',
  chunk_count: 1,
  error_message: null,
  created_at: '2026-09-21T00:00:00Z',
  updated_at: '2026-09-21T00:00:00Z',
}

const uploadedDocument: DocumentItem = {
  ...builtinDocument,
  id: 2,
  file_name: '用户上传资料.pdf',
  source_type: 'UPLOAD',
  source_key: null,
}

describe('DocumentList', () => {
  it('在归属列展示资料来源，不再把来源标签放在文件名后', () => {
    const wrapper = shallowMount(DocumentList, {
      props: {
        documents: [builtinDocument, uploadedDocument],
        loading: false,
        page: 1,
        size: 10,
        total: 2,
        keyword: '',
        status: '',
      },
      global: {
        stubs: {
          ElButton: true,
          ElInput: true,
          ElOption: true,
          ElPagination: true,
          ElSelect: true,
          ElTag: { template: '<span class="tag-stub"><slot /></span>' },
        },
      },
    })

    expect(wrapper.findAll('th').map((header) => header.text())).toContain('归属')
    expect(wrapper.findAll('.document-name-cell .tag-stub')).toHaveLength(0)
    expect(wrapper.findAll('.document-source-cell .tag-stub')).toHaveLength(2)
    expect(wrapper.text()).toContain('系统资料')
    expect(wrapper.text()).toContain('用户上传')
  })
})
