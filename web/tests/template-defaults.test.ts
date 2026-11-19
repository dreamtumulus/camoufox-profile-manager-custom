import assert from 'node:assert/strict'
import test from 'node:test'
import { detectTemplateOs, uniqueTemplateName } from '../src/lib/template-defaults'

test('a new template starts on the desktop system the interface runs on', () => {
  assert.equal(detectTemplateOs('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0'), 'windows')
  assert.equal(detectTemplateOs('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)'), 'macos')
  assert.equal(detectTemplateOs('Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'), 'linux')
  assert.equal(detectTemplateOs('Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0'), 'linux')
})

test('phones, tablets and unknown agents keep the Windows default', () => {
  // Android says "Linux" and iOS says "Mac OS X"; neither is a desktop template system.
  assert.equal(detectTemplateOs('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36'), 'windows')
  assert.equal(detectTemplateOs('Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148'), 'windows')
  for (const agent of ['', 'curl/8.4.0', null, undefined]) assert.equal(detectTemplateOs(agent), 'windows')
})

test('suggested names skip the names saved templates already use', () => {
  assert.equal(uniqueTemplateName('macOS template', []), 'macOS template')
  assert.equal(uniqueTemplateName('macOS template', ['Work', 'Linux template']), 'macOS template')
  assert.equal(uniqueTemplateName('macOS template', ['  macos TEMPLATE ']), 'macOS template 2')
  assert.equal(uniqueTemplateName('macOS 模板', ['macOS 模板', 'macOS 模板 2']), 'macOS 模板 3')
  assert.equal(uniqueTemplateName('macOS template', ['macOS template 2']), 'macOS template')
})
