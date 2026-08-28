import { describe, expect, it } from 'vitest'

import {
  normalizeShivaOpenString,
  pathFromShivaDeepLink,
  pathFromOpenDeepLink,
  resolveShivaOpenPath
} from './shiva-open-target'

describe('normalizeShivaOpenString', () => {
  it('accepts hash-router paths and strips a leading hash', () => {
    expect(normalizeShivaOpenString('/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeShivaOpenString('#/index-network/intent/1')).toBe('/index-network/intent/1')
  })

  it('maps plugin-scoped shiva:// deep links to the same path', () => {
    expect(normalizeShivaOpenString('shiva://index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeShivaOpenString('shiva://index-network/intent/1?focus=true')).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('maps shiva://open/… deep links by stripping the open host', () => {
    expect(normalizeShivaOpenString('shiva://open/index-network/intent/1')).toBe('/index-network/intent/1')
    expect(normalizeShivaOpenString('shiva://open/settings/plugins')).toBe('/settings/plugins')
  })

  it('rejects reserved shiva kinds and unsafe paths', () => {
    expect(normalizeShivaOpenString('shiva://blueprint/morning-brief')).toBeNull()
    expect(normalizeShivaOpenString('shiva://plugin/install')).toBeNull()
    expect(normalizeShivaOpenString('https://example.com/x')).toBeNull()
    expect(normalizeShivaOpenString('/../etc/passwd')).toBeNull()
    expect(normalizeShivaOpenString('index-network')).toBeNull()
  })
})

describe('resolveShivaOpenPath', () => {
  it('merges structured path + params', () => {
    expect(resolveShivaOpenPath({ path: '/index-network/intent/1', params: { focus: 'true' } })).toBe(
      '/index-network/intent/1?focus=true'
    )
  })

  it('resolves href the same as a bare string', () => {
    expect(resolveShivaOpenPath({ href: 'shiva://index-network/intent/1' })).toBe('/index-network/intent/1')
  })
})

describe('pathFromShivaDeepLink', () => {
  it('builds the navigate path from a plugin-scoped deep-link payload', () => {
    expect(pathFromShivaDeepLink('index-network', 'intent/1')).toBe('/index-network/intent/1')
  })

  it('builds the navigate path from shiva://open/… payloads', () => {
    expect(pathFromOpenDeepLink('index-network/intent/1')).toBe('/index-network/intent/1')
    expect(pathFromShivaDeepLink('open', 'agent/42')).toBe('/agent/42')
  })

  it('ignores reserved kinds', () => {
    expect(pathFromShivaDeepLink('blueprint', 'morning-brief')).toBeNull()
    expect(pathFromShivaDeepLink('plugin', 'install')).toBeNull()
  })
})
