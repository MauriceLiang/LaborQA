// 文件作用：提供 Markdown 内容清理、解析和展示辅助逻辑。
/** Markdown 正文中识别出的行内文本、强调、代码或换行标记。 */
export type MarkdownInlineToken =
  | { type: 'text'; text: string }
  | { type: 'strong'; text: string }
  | { type: 'emphasis'; text: string }
  | { type: 'code'; text: string }
  | { type: 'line-break' }

/** Markdown 解析后形成的段落、标题、列表、引用或代码块。 */
export type MarkdownBlock =
  | { type: 'paragraph'; tokens: MarkdownInlineToken[] }
  | { type: 'heading'; level: number; tokens: MarkdownInlineToken[] }
  | { type: 'unordered-list'; items: MarkdownInlineToken[][] }
  | { type: 'ordered-list'; items: MarkdownInlineToken[][] }
  | { type: 'quote'; tokens: MarkdownInlineToken[] }
  | { type: 'code'; code: string }

// 支持的行内语法定界符及其解析后标记类型，顺序用于优先匹配较长标记。
const inlineMarkers = [
  { close: '**', open: '**', type: 'strong' as const },
  { close: '__', open: '__', type: 'strong' as const },
  { close: '`', open: '`', type: 'code' as const },
  { close: '*', open: '*', type: 'emphasis' as const },
  { close: '_', open: '_', type: 'emphasis' as const },
]

/** 判断字符是否可通过反斜杠转义。
 * @param value 当前待检查的字符。
 * @returns 可转义时为 true。
 */
function isEscapableCharacter(value: string) {
  return '*_`\\'.includes(value)
}

/** 将一行文本解析为行内标记，支持强调、行内代码、转义和换行。
 * @param value 待解析的 Markdown 文本。
 * @returns 按原始顺序排列的行内标记数组。
 */
export function parseInlineMarkdown(value: string): MarkdownInlineToken[] {
  // tokens 按顺序累积解析结果。
  const tokens: MarkdownInlineToken[] = []
  // plainText 暂存尚未遇到格式边界的普通文本。
  let plainText = ''

  // 将暂存的普通文本输出为一个 text 标记并清空缓冲区。
  const flushPlainText = () => {
    if (!plainText) return
    tokens.push({ type: 'text', text: plainText })
    plainText = ''
  }

  // index 指向当前扫描到的字符位置。
  let index = 0
  while (index < value.length) {
    // 当前扫描位置的字符。
    const character = value[index]

    if (character === '\n') {
      flushPlainText()
      tokens.push({ type: 'line-break' })
      index += 1
      continue
    }

    if (character === '\\' && isEscapableCharacter(value[index + 1] ?? '')) {
      plainText += value[index + 1]
      index += 2
      continue
    }

    // marker 是当前字符位置匹配到的行内语法定界符。
    const marker = inlineMarkers.find(({ open }) => value.startsWith(open, index))
    if (!marker) {
      plainText += character
      index += 1
      continue
    }

    // contentStart 是定界符之后的内容起始位置。
    const contentStart = index + marker.open.length
    // contentEnd 是对应结束定界符的位置；未配对时为 -1。
    const contentEnd = value.indexOf(marker.close, contentStart)
    if (contentEnd <= contentStart || /^\s|\s$/.test(value.slice(contentStart, contentEnd))) {
      plainText += marker.open
      index += marker.open.length
      continue
    }

    flushPlainText()
    tokens.push({ type: marker.type, text: value.slice(contentStart, contentEnd) })
    index = contentEnd + marker.close.length
  }

  flushPlainText()
  return tokens
}

type ListKind = 'unordered-list' | 'ordered-list'

/** 识别一行是否为有序或无序列表项。
 * @param line 当前待识别的 Markdown 行。
 * @returns 列表类型与去除标记后的内容；非列表行返回 null。
 */
function listMatch(line: string): { kind: ListKind; content: string } | null {
  // unordered 保存匹配无序列表标记后的捕获结果。
  const unordered = line.match(/^\s*[-*+•–—]\s+(.+)$/)
  if (unordered) return { kind: 'unordered-list', content: unordered[1] }

  // ordered 保存匹配数字编号列表标记后的捕获结果。
  const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/)
  if (ordered) return { kind: 'ordered-list', content: ordered[1] }

  return null
}

/** 将完整 Markdown 文本解析为段落、标题、列表、引用和代码块。
 * @param source 待解析的原始 Markdown 字符串。
 * @returns 保留内容顺序的块级结构数组。
 */
export function parseMarkdown(source: string): MarkdownBlock[] {
  // 统一换行后拆分得到的源文本行。
  const lines = source.replaceAll('\r\n', '\n').replaceAll('\r', '\n').split('\n')
  // blocks 按输入顺序保存最终解析出的块级内容。
  const blocks: MarkdownBlock[] = []
  // paragraphLines 暂存当前段落的连续文本行。
  let paragraphLines: string[] = []
  // listKind 记录当前列表类型，类型变化时需要结束当前列表块。
  let listKind: ListKind | null = null
  // listItems 暂存当前列表中已解析的各列表项。
  let listItems: MarkdownInlineToken[][] = []
  // codeLines 在围栏代码块中保存原始代码行；非代码块时为空。
  let codeLines: string[] | null = null

  // 将当前段落缓冲内容解析并追加到块列表。
  const flushParagraph = () => {
    if (!paragraphLines.length) return
    blocks.push({ type: 'paragraph', tokens: parseInlineMarkdown(paragraphLines.join('\n')) })
    paragraphLines = []
  }

  // 将当前列表缓冲内容输出为对应类型的列表块。
  const flushList = () => {
    if (!listKind) return
    blocks.push({ type: listKind, items: listItems })
    listKind = null
    listItems = []
  }

  // 结束普通文本区域中未完成的段落和列表。
  const flushTextBlocks = () => {
    flushParagraph()
    flushList()
  }

  // line 是当前遍历到的 Markdown 源文本行。
  for (const line of lines) {
    // fence 表示当前行是否为代码围栏标记。
    const fence = line.match(/^\s*```(?:\w+)?\s*$/)
    if (fence) {
      if (codeLines) {
        blocks.push({ type: 'code', code: codeLines.join('\n') })
        codeLines = null
      } else {
        flushTextBlocks()
        codeLines = []
      }
      continue
    }

    if (codeLines) {
      codeLines.push(line)
      continue
    }

    if (!line.trim()) {
      flushTextBlocks()
      continue
    }

    // heading 保存标题标记及标题正文的匹配结果。
    const heading = line.match(/^\s*(#{1,6})\s+(.+?)\s*#*\s*$/)
    if (heading) {
      flushTextBlocks()
      blocks.push({
        type: 'heading',
        level: heading[1].length,
        tokens: parseInlineMarkdown(heading[2]),
      })
      continue
    }

    // quote 保存引用标记及引用正文的匹配结果。
    const quote = line.match(/^\s*>\s?(.*)$/)
    if (quote) {
      flushTextBlocks()
      blocks.push({ type: 'quote', tokens: parseInlineMarkdown(quote[1]) })
      continue
    }

    // list 保存当前行的列表类型和去掉前缀后的内容。
    const list = listMatch(line)
    if (list) {
      flushParagraph()
      if (listKind !== list.kind) {
        flushList()
        listKind = list.kind
      }
      listItems.push(parseInlineMarkdown(list.content))
      continue
    }

    flushList()
    paragraphLines.push(line)
  }

  if (codeLines) blocks.push({ type: 'code', code: codeLines.join('\n') })
  flushTextBlocks()
  return blocks
}
