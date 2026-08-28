import { contextBridge, ipcRenderer, webFrame, webUtils } from 'electron'

// Which translucency the OS can back. Asked synchronously because the renderer
// needs it before its first paint, and answered by main because deciding it
// needs `os.release()` — a sandboxed preload may only require electron, events,
// timers and url, so importing node:os here throws before contextBridge runs
// and takes the ENTIRE bridge down with it (window.shivaDesktop undefined =>
// "Desktop IPC bridge is unavailable"). No reply means no glass, which degrades
// to an ordinary opaque window rather than a page thinned over nothing.
const translucencySupport = ipcRenderer.sendSync('shiva:translucency:support')
const hudWindowing = ipcRenderer.sendSync('shiva:hud:windowing')
const hudNativeDrag = hudWindowing?.nativeDrag === true

contextBridge.exposeInMainWorld('shivaDesktop', {
  glassSupported: translucencySupport?.glass === true,
  translucencySupported: translucencySupport?.translucency === true,
  getConnection: profile => ipcRenderer.invoke('shiva:connection', profile),
  // Registry-scoped backend resolution: { connectionId, profile } → descriptor.
  getConnectionFor: payload => ipcRenderer.invoke('shiva:connection:for', payload),
  getProfileRoutes: profiles => ipcRenderer.invoke('shiva:plugin-profile-routes', profiles),
  revalidateConnection: () => ipcRenderer.invoke('shiva:connection:revalidate'),
  touchBackend: profile => ipcRenderer.invoke('shiva:backend:touch', profile),
  getGatewayWsUrl: profile => ipcRenderer.invoke('shiva:gateway:ws-url', profile),
  // Registry-scoped fresh WS URL: { connectionId, profile } → result shape of
  // getGatewayWsUrl, minted against that connection's backend.
  getGatewayWsUrlFor: payload => ipcRenderer.invoke('shiva:gateway:ws-url-for', payload),
  // Union agent roster across every registered connection.
  getAgentRoster: () => ipcRenderer.invoke('shiva:agents:roster'),
  openSessionWindow: (sessionId, opts) => ipcRenderer.invoke('shiva:window:openSession', sessionId, opts),
  openSessionInTerminal: (sessionId, opts) => ipcRenderer.invoke('shiva:window:openInTerminal', sessionId, opts),
  openWindow: () => ipcRenderer.invoke('shiva:window:openInstance'),
  openBrowserWindow: tabId => ipcRenderer.invoke('shiva:window:openBrowser', tabId),
  onBrowserPopoutClosed: callback => {
    const listener = (_event, tabId) => callback(tabId)
    ipcRenderer.on('shiva:browser-popout:closed', listener)

    return () => ipcRenderer.removeListener('shiva:browser-popout:closed', listener)
  },
  claimAmbientCue: key => ipcRenderer.invoke('shiva:ambient:claim', key),
  wakeIndicator: {
    getState: () => ipcRenderer.invoke('shiva:wake-indicator:get'),
    setState: state => ipcRenderer.send('shiva:wake-indicator:set', state),
    onState: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('shiva:wake-indicator:state', listener)

      return () => ipcRenderer.removeListener('shiva:wake-indicator:state', listener)
    }
  },
  petOverlay: {
    // Main renderer → main process: window lifecycle + drag. `request` is
    // `{ bounds, screen }`; resolves with the screen bounds it actually used.
    open: request => ipcRenderer.invoke('shiva:pet-overlay:open', request),
    close: () => ipcRenderer.invoke('shiva:pet-overlay:close'),
    setBounds: bounds => ipcRenderer.send('shiva:pet-overlay:set-bounds', bounds),
    setIgnoreMouse: ignore => ipcRenderer.send('shiva:pet-overlay:ignore-mouse', ignore),
    // Flip the overlay focusable (and focus it) while the composer needs keys.
    setFocusable: focusable => ipcRenderer.send('shiva:pet-overlay:set-focusable', focusable),
    // Main renderer → overlay (forwarded by main): push the latest pet state.
    pushState: payload => ipcRenderer.send('shiva:pet-overlay:state', payload),
    // Overlay → main renderer (forwarded by main): pop back in / composer submit.
    control: payload => ipcRenderer.send('shiva:pet-overlay:control', payload),
    // Overlay subscribes to state pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:pet-overlay:state', listener)

      return () => ipcRenderer.removeListener('shiva:pet-overlay:state', listener)
    },
    // Main renderer subscribes to overlay control messages.
    onControl: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:pet-overlay:control', listener)

      return () => ipcRenderer.removeListener('shiva:pet-overlay:control', listener)
    }
  },
  // HUD mode: the chrome-free floating chat. A full app renderer (own gateway)
  // sized as a floating bar, so it mounts the real composer. Main owns the
  // window; `onChanged` keeps every window's toggle truthful.
  hud: {
    nativeDrag: hudNativeDrag,
    windowing: {
      clientPlacement: hudWindowing?.clientPlacement !== false,
      controlDrag: hudWindowing?.controlDrag === true,
      nativeDrag: hudNativeDrag,
      workspaceTransfer: hudWindowing?.workspaceTransfer === true
    },
    open: request => ipcRenderer.invoke('shiva:hud:open', request),
    close: () => ipcRenderer.invoke('shiva:hud:close'),
    setIgnoreMouse: ignore => ipcRenderer.send('shiva:hud:ignore-mouse', ignore),
    moveBy: delta => ipcRenderer.send('shiva:hud:move-by', delta),
    setWorkspaceTransfer: transferring => ipcRenderer.send('shiva:hud:workspace-transfer', transferring),
    setBounds: bounds => ipcRenderer.send('shiva:hud:set-bounds', bounds),
    resetLayout: () => ipcRenderer.invoke('shiva:hud:reset-layout'),
    // Whether the band covers the window below the bar. Main pairs it with the
    // user's translucency setting to decide the native frost (macOS vibrancy /
    // Windows 11 DWM backdrop) — see hudFrostFor.
    setFrost: showing => ipcRenderer.invoke('shiva:hud:frost', showing),
    // The HUD tells main which session it is on; main hands that back to the
    // app window when the HUD closes, so the app can re-home onto it.
    setSession: sessionId => ipcRenderer.send('shiva:hud:session', sessionId),
    onGoto: callback => {
      const listener = (_event, sessionId) => callback(sessionId)
      ipcRenderer.on('shiva:hud:goto', listener)

      return () => ipcRenderer.removeListener('shiva:hud:goto', listener)
    },
    onChanged: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('shiva:hud:changed', listener)

      return () => ipcRenderer.removeListener('shiva:hud:changed', listener)
    },
    // Linux only, and silent elsewhere: where the cursor is, in page
    // coordinates, or null when it has left the window. Stands in for the
    // mousemove that `setIgnoreMouseEvents(true, { forward: true })` delivers on
    // macOS and Windows but not here.
    onCursor: callback => {
      const listener = (_event, point) => callback(point)
      ipcRenderer.on('shiva:hud:cursor', listener)

      return () => ipcRenderer.removeListener('shiva:hud:cursor', listener)
    },
    // Main's game-overlay watch: whether a fullscreen app (a game) is under
    // the HUD, so the renderer can step back to the low-opacity overlay
    // treatment while one owns the screen.
    onGameOverlay: callback => {
      const listener = (_event, state) => callback(state)
      ipcRenderer.on('shiva:hud:game-overlay', listener)

      return () => ipcRenderer.removeListener('shiva:hud:game-overlay', listener)
    }
  },
  // Quick Entry: the global-hotkey mini composer window. Main owns the OS
  // shortcut + the persisted preference; the quick window only captures text
  // and hands it back, and the primary renderer submits it through the normal
  // prompt path.
  quickEntry: {
    getSettings: () => ipcRenderer.invoke('shiva:quick-entry:settings:get'),
    setSettings: patch => ipcRenderer.invoke('shiva:quick-entry:settings:set', patch),
    submit: payload => ipcRenderer.send('shiva:quick-entry:submit', payload),
    dismiss: () => ipcRenderer.send('shiva:quick-entry:dismiss'),
    // Primary renderer → main → quick window: gateway connection state + the
    // recent-session options the target picker offers. Main caches the latest
    // payload so a freshly spawned quick window starts from truth.
    pushState: payload => ipcRenderer.send('shiva:quick-entry:state', payload),
    // Quick window subscribes to those pushes.
    onState: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:quick-entry:state', listener)

      return () => ipcRenderer.removeListener('shiva:quick-entry:state', listener)
    },
    // Main → primary renderer: a submit captured by the quick window.
    onSubmit: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:quick-entry:submit', listener)

      return () => ipcRenderer.removeListener('shiva:quick-entry:submit', listener)
    },
    // Main → quick window: you were just summoned (reset draft + refocus).
    onShown: callback => {
      const listener = () => callback()
      ipcRenderer.on('shiva:quick-entry:shown', listener)

      return () => ipcRenderer.removeListener('shiva:quick-entry:shown', listener)
    }
  },
  getBootProgress: () => ipcRenderer.invoke('shiva:boot-progress:get'),
  getConnectionConfig: profile => ipcRenderer.invoke('shiva:connection-config:get', profile),
  saveConnectionConfig: payload => ipcRenderer.invoke('shiva:connection-config:save', payload),
  applyConnectionConfig: payload => ipcRenderer.invoke('shiva:connection-config:apply', payload),
  testConnectionConfig: payload => ipcRenderer.invoke('shiva:connection-config:test', payload),
  // Opt-in OS-keychain encryption for stored gateway secrets (default off —
  // see secret-storage-policy.ts). get never touches the OS keychain.
  getSecretStorageEncryption: () => ipcRenderer.invoke('shiva:secret-storage:get'),
  setSecretStorageEncryption: (on: boolean) => ipcRenderer.invoke('shiva:secret-storage:set', on),
  // v2 multi-connection registry: named agent sources (local / remote / cloud / ssh).
  connections: {
    list: () => ipcRenderer.invoke('shiva:connections:list'),
    save: payload => ipcRenderer.invoke('shiva:connections:save', payload),
    remove: id => ipcRenderer.invoke('shiva:connections:remove', id),
    setPrimary: id => ipcRenderer.invoke('shiva:connections:set-primary', id),
    setLaunchMode: mode => ipcRenderer.invoke('shiva:connections:set-launch-mode', mode),
    setLastUsed: id => ipcRenderer.invoke('shiva:connections:set-last-used', id),
    test: id => ipcRenderer.invoke('shiva:connections:test', id),
    updateManaged: id => ipcRenderer.invoke('shiva:connections:update-managed', id),
    // Fan out `shiva update` to every eligible registered connection.
    // Optional excludeIds skips rows the caller updates through another path.
    updateAll: options => ipcRenderer.invoke('shiva:connections:update-all', options),
    // Registry lifecycle push (main → renderer): a connection was removed or
    // materially edited, so secondaries scoped to it must be disposed (and,
    // for edits, re-dialed at the new target).
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:connections:changed', listener)

      return () => ipcRenderer.removeListener('shiva:connections:changed', listener)
    }
  },
  sshConfigHosts: () => ipcRenderer.invoke('shiva:ssh-config:hosts'),
  sshResolveHost: host => ipcRenderer.invoke('shiva:ssh-config:resolve', host),
  probeConnectionConfig: remoteUrl => ipcRenderer.invoke('shiva:connection-config:probe', remoteUrl),
  oauthLoginConnectionConfig: remoteUrl => ipcRenderer.invoke('shiva:connection-config:oauth-login', remoteUrl),
  oauthLogoutConnectionConfig: remoteUrl => ipcRenderer.invoke('shiva:connection-config:oauth-logout', remoteUrl),
  // Shiva Cloud: one portal login powers discovery + silent per-agent sign-in
  // (cloud-auto-discovery Phase 3).
  cloud: {
    status: () => ipcRenderer.invoke('shiva:cloud:status'),
    login: () => ipcRenderer.invoke('shiva:cloud:login'),
    logout: () => ipcRenderer.invoke('shiva:cloud:logout'),
    discover: org => ipcRenderer.invoke('shiva:cloud:discover', org),
    agentSignIn: dashboardUrl => ipcRenderer.invoke('shiva:cloud:agent-sign-in', dashboardUrl)
  },
  profile: {
    get: () => ipcRenderer.invoke('shiva:profile:get'),
    remember: name => ipcRenderer.invoke('shiva:profile:remember', name),
    set: name => ipcRenderer.invoke('shiva:profile:set', name)
  },
  api: request => ipcRenderer.invoke('shiva:api', request),
  notify: payload => ipcRenderer.invoke('shiva:notify', payload),
  requestMicrophoneAccess: () => ipcRenderer.invoke('shiva:requestMicrophoneAccess'),
  readWindowBelow: () => ipcRenderer.invoke('shiva:window:readBelow'),
  readFileDataUrl: filePath => ipcRenderer.invoke('shiva:readFileDataUrl', filePath),
  readFileDataUrlForAttach: filePath => ipcRenderer.invoke('shiva:readFileDataUrlForAttach', filePath),
  dataUrlReadMax: {
    get: () => ipcRenderer.invoke('shiva:data-url-read-max:get'),
    set: maxMb => ipcRenderer.invoke('shiva:data-url-read-max:set', maxMb)
  },
  readFileText: filePath => ipcRenderer.invoke('shiva:readFileText', filePath),
  readPluginSource: (filePath: string) => ipcRenderer.invoke('shiva:readPluginSource', filePath),
  selectPaths: options => ipcRenderer.invoke('shiva:selectPaths', options),
  selectSavePath: options => ipcRenderer.invoke('shiva:selectSavePath', options),
  writeClipboard: text => ipcRenderer.invoke('shiva:writeClipboard', text),
  readClipboard: () => ipcRenderer.invoke('shiva:readClipboard'),
  saveGatewayFile: payload => ipcRenderer.invoke('shiva:saveGatewayFile', payload),
  saveImageFromUrl: url => ipcRenderer.invoke('shiva:saveImageFromUrl', url),
  contextMenuEdit: command => ipcRenderer.invoke('shiva:context-menu:edit', command),
  contextMenuCopyImage: () => ipcRenderer.invoke('shiva:context-menu:copy-image'),
  contextMenuSpellcheck: action => ipcRenderer.invoke('shiva:context-menu:spellcheck', action),
  contextMenuGuestAddWord: payload => ipcRenderer.invoke('shiva:context-menu:guest-add-word', payload),
  onContextMenuSpellcheck: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:context-menu-spellcheck', listener)

    return () => ipcRenderer.removeListener('shiva:context-menu-spellcheck', listener)
  },
  saveImageBuffer: (data, ext) => ipcRenderer.invoke('shiva:saveImageBuffer', { data, ext }),
  saveClipboardImage: () => ipcRenderer.invoke('shiva:saveClipboardImage'),
  getPathForFile: file => {
    try {
      return webUtils.getPathForFile(file) || ''
    } catch {
      return ''
    }
  },
  normalizePreviewTarget: (target, baseDir) => ipcRenderer.invoke('shiva:normalizePreviewTarget', target, baseDir),
  watchPreviewFile: url => ipcRenderer.invoke('shiva:watchPreviewFile', url),
  watchDirectory: dir => ipcRenderer.invoke('shiva:watchDirectory', dir),
  stopPreviewFileWatch: id => ipcRenderer.invoke('shiva:stopPreviewFileWatch', id),
  setActiveWork: payload => ipcRenderer.send('shiva:active-work', payload),
  setTitleBarTheme: payload => ipcRenderer.send('shiva:titlebar-theme', payload),
  setNativeTheme: mode => ipcRenderer.send('shiva:native-theme', mode),
  setTranslucency: payload => ipcRenderer.send('shiva:translucency', payload),
  setKeepAwake: on => ipcRenderer.send('shiva:keep-awake', on),
  setDisableF12: blocked => ipcRenderer.send('shiva:devtools:disable-f12', blocked),
  setPreviewShortcutActive: active => ipcRenderer.send('shiva:previewShortcutActive', Boolean(active)),
  openExternal: url => ipcRenderer.invoke('shiva:openExternal', url),
  openPreviewInBrowser: url => ipcRenderer.invoke('shiva:openPreviewInBrowser', url),
  reachPreviewUrl: url => ipcRenderer.invoke('shiva:preview:reach', url),
  setActiveConnectionRoute: route => ipcRenderer.send('shiva:connection:active-route', route),
  fetchLinkTitle: url => ipcRenderer.invoke('shiva:fetchLinkTitle', url),
  resolveFavicon: url => ipcRenderer.invoke('shiva:resolveFavicon', url),
  sanitizeWorkspaceCwd: cwd => ipcRenderer.invoke('shiva:workspace:sanitize', cwd),
  settings: {
    getDefaultProjectDir: () => ipcRenderer.invoke('shiva:setting:defaultProjectDir:get'),
    setDefaultProjectDir: dir => ipcRenderer.invoke('shiva:setting:defaultProjectDir:set', dir),
    pickDefaultProjectDir: () => ipcRenderer.invoke('shiva:setting:defaultProjectDir:pick')
  },
  zoom: {
    // Current zoom of this window, as { level, percent }.
    get: () => ipcRenderer.invoke('shiva:zoom:get'),
    // Synchronous zoom factor (1 = 100%). Coordinate math needs it in the
    // same tick as the event it converts, so no IPC round-trip here.
    factor: () => webFrame.getZoomFactor(),
    setPercent: percent => ipcRenderer.send('shiva:zoom:set-percent', percent),
    // Fires on every zoom change, including the Ctrl/Cmd +/-/0 shortcuts,
    // so the settings UI can stay in sync with the keyboard.
    onChanged: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:zoom:changed', listener)

      return () => ipcRenderer.removeListener('shiva:zoom:changed', listener)
    }
  },
  revealLogs: () => ipcRenderer.invoke('shiva:logs:reveal'),
  getRecentLogs: () => ipcRenderer.invoke('shiva:logs:recent'),
  // Fire-and-forget: persists a renderer error-boundary catch (with component
  // stack) to desktop.log so crashes survive the window (#79428).
  reportRendererError: report => ipcRenderer.send('shiva:logs:renderer-error', report),
  readDir: dirPath => ipcRenderer.invoke('shiva:fs:readDir', dirPath),
  gitRoot: startPath => ipcRenderer.invoke('shiva:fs:gitRoot', startPath),
  revealPath: targetPath => ipcRenderer.invoke('shiva:fs:reveal', targetPath),
  openDir: dirPath => ipcRenderer.invoke('shiva:fs:openDir', dirPath),
  desktopPluginsRoot: () => ipcRenderer.invoke('shiva:fs:desktopPluginsRoot'),
  logsRoot: () => ipcRenderer.invoke('shiva:fs:logsRoot'),
  agentPluginsRoot: () => ipcRenderer.invoke('shiva:fs:agentPluginsRoot'),
  renamePath: (targetPath, newName) => ipcRenderer.invoke('shiva:fs:rename', targetPath, newName),
  writeTextFile: (filePath, content) => ipcRenderer.invoke('shiva:fs:writeText', filePath, content),
  trashPath: targetPath => ipcRenderer.invoke('shiva:fs:trash', targetPath),
  git: {
    worktreeList: repoPath => ipcRenderer.invoke('shiva:git:worktreeList', repoPath),
    worktreeAdd: (repoPath, options) => ipcRenderer.invoke('shiva:git:worktreeAdd', repoPath, options),
    worktreeRemove: (repoPath, worktreePath, options) =>
      ipcRenderer.invoke('shiva:git:worktreeRemove', repoPath, worktreePath, options),
    branchSwitch: (repoPath, branch) => ipcRenderer.invoke('shiva:git:branchSwitch', repoPath, branch),
    branchList: repoPath => ipcRenderer.invoke('shiva:git:branchList', repoPath),
    baseBranchList: repoPath => ipcRenderer.invoke('shiva:git:baseBranchList', repoPath),
    repoStatus: repoPath => ipcRenderer.invoke('shiva:git:repoStatus', repoPath),
    fileDiff: (repoPath, filePath) => ipcRenderer.invoke('shiva:git:fileDiff', repoPath, filePath),
    scanRepos: (roots, options) => ipcRenderer.invoke('shiva:git:scanRepos', roots, options),
    review: {
      list: (repoPath, scope, baseRef) => ipcRenderer.invoke('shiva:git:review:list', repoPath, scope, baseRef),
      diff: (repoPath, filePath, scope, baseRef, staged) =>
        ipcRenderer.invoke('shiva:git:review:diff', repoPath, filePath, scope, baseRef, staged),
      stage: (repoPath, filePath) => ipcRenderer.invoke('shiva:git:review:stage', repoPath, filePath),
      unstage: (repoPath, filePath) => ipcRenderer.invoke('shiva:git:review:unstage', repoPath, filePath),
      revert: (repoPath, filePath) => ipcRenderer.invoke('shiva:git:review:revert', repoPath, filePath),
      revParse: (repoPath, ref) => ipcRenderer.invoke('shiva:git:review:revParse', repoPath, ref),
      commit: (repoPath, message, push) => ipcRenderer.invoke('shiva:git:review:commit', repoPath, message, push),
      commitContext: repoPath => ipcRenderer.invoke('shiva:git:review:commitContext', repoPath),
      push: repoPath => ipcRenderer.invoke('shiva:git:review:push', repoPath),
      shipInfo: repoPath => ipcRenderer.invoke('shiva:git:review:shipInfo', repoPath),
      prList: (repoPath, branches, numbers) =>
        ipcRenderer.invoke('shiva:git:review:prList', repoPath, branches, numbers),
      fetchPrComment: (repoPath, url) => ipcRenderer.invoke('shiva:git:review:fetchPrComment', repoPath, url),
      createPr: repoPath => ipcRenderer.invoke('shiva:git:review:createPr', repoPath)
    }
  },
  terminal: {
    cwd: id => ipcRenderer.invoke('shiva:terminal:cwd', id),
    dispose: id => ipcRenderer.invoke('shiva:terminal:dispose', id),
    resize: (id, size) => ipcRenderer.invoke('shiva:terminal:resize', id, size),
    start: options => ipcRenderer.invoke('shiva:terminal:start', options),
    write: (id, data) => ipcRenderer.invoke('shiva:terminal:write', id, data),
    onData: (id, callback) => {
      const channel = `shiva:terminal:${id}:data`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    },
    onExit: (id, callback) => {
      const channel = `shiva:terminal:${id}:exit`
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on(channel, listener)

      return () => ipcRenderer.removeListener(channel, listener)
    }
  },
  onClosePreviewRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('shiva:close-preview-requested', listener)

    return () => ipcRenderer.removeListener('shiva:close-preview-requested', listener)
  },
  onPreviewNav: callback => {
    const listener = (_event, command) => callback(command)
    ipcRenderer.on('shiva:preview-nav', listener)

    return () => ipcRenderer.removeListener('shiva:preview-nav', listener)
  },
  onOpenFolderRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('shiva:open-folder-requested', listener)

    return () => ipcRenderer.removeListener('shiva:open-folder-requested', listener)
  },
  onOpenUpdatesRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('shiva:open-updates', listener)

    return () => ipcRenderer.removeListener('shiva:open-updates', listener)
  },
  onDeepLink: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:deep-link', listener)

    return () => ipcRenderer.removeListener('shiva:deep-link', listener)
  },
  signalDeepLinkReady: () => ipcRenderer.invoke('shiva:deep-link-ready'),
  probePluginRepo: payload => ipcRenderer.invoke('shiva:plugin:probe', payload),
  installDesktopPlugin: payload => ipcRenderer.invoke('shiva:plugin:installDesktop', payload),
  onWindowStateChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:window-state-changed', listener)

    return () => ipcRenderer.removeListener('shiva:window-state-changed', listener)
  },
  onFocusSession: callback => {
    const listener = (_event, sessionId) => callback(sessionId)
    ipcRenderer.on('shiva:focus-session', listener)

    return () => ipcRenderer.removeListener('shiva:focus-session', listener)
  },
  onNotificationAction: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:notification-action', listener)

    return () => ipcRenderer.removeListener('shiva:notification-action', listener)
  },
  onNotificationActivate: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:notification-activate', listener)

    return () => ipcRenderer.removeListener('shiva:notification-activate', listener)
  },
  onPreviewFileChanged: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:preview-file-changed', listener)

    return () => ipcRenderer.removeListener('shiva:preview-file-changed', listener)
  },
  onBackendExit: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:backend-exit', listener)

    return () => ipcRenderer.removeListener('shiva:backend-exit', listener)
  },
  // Soft gateway-mode apply finished tearing down the primary backend. Renderer
  // should wipe session lists + re-dial without a window reload.
  onConnectionApplied: callback => {
    const listener = () => callback()
    ipcRenderer.on('shiva:connection:applied', listener)

    return () => ipcRenderer.removeListener('shiva:connection:applied', listener)
  },
  onPowerResume: callback => {
    const listener = () => callback()
    ipcRenderer.on('shiva:power-resume', listener)

    return () => ipcRenderer.removeListener('shiva:power-resume', listener)
  },
  // AC ↔ battery transitions; renderers slow their backstop polls on battery.
  getOnBattery: () => ipcRenderer.invoke('shiva:power-battery:get'),
  onBatteryChanged: callback => {
    const listener = (_event, onBattery) => callback(Boolean(onBattery))
    ipcRenderer.on('shiva:power-battery', listener)

    return () => ipcRenderer.removeListener('shiva:power-battery', listener)
  },
  onBootProgress: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:boot-progress', listener)

    return () => ipcRenderer.removeListener('shiva:boot-progress', listener)
  },
  // First-launch bootstrap progress -- emitted by the install.ps1 stage
  // runner in main.ts (apps/desktop/electron/bootstrap-runner.ts).
  // Renderer's install overlay subscribes to live events and queries the
  // current snapshot via getBootstrapState() to recover after a devtools
  // reload mid-bootstrap.
  getBootstrapState: () => ipcRenderer.invoke('shiva:bootstrap:get'),
  continueBootstrapLocal: () => ipcRenderer.invoke('shiva:bootstrap:continue-local'),
  resetBootstrap: () => ipcRenderer.invoke('shiva:bootstrap:reset'),
  repairBootstrap: () => ipcRenderer.invoke('shiva:bootstrap:repair'),
  cancelBootstrap: () => ipcRenderer.invoke('shiva:bootstrap:cancel'),
  onBootstrapEvent: callback => {
    const listener = (_event, payload) => callback(payload)
    ipcRenderer.on('shiva:bootstrap:event', listener)

    return () => ipcRenderer.removeListener('shiva:bootstrap:event', listener)
  },
  getVersion: () => ipcRenderer.invoke('shiva:version'),
  getRemoteDisplayReason: () => ipcRenderer.invoke('shiva:get-remote-display-reason'),
  uninstall: {
    summary: () => ipcRenderer.invoke('shiva:uninstall:summary'),
    run: mode => ipcRenderer.invoke('shiva:uninstall:run', { mode })
  },
  updates: {
    check: () => ipcRenderer.invoke('shiva:updates:check'),
    apply: opts => ipcRenderer.invoke('shiva:updates:apply', opts),
    getBranch: () => ipcRenderer.invoke('shiva:updates:branch:get'),
    setBranch: name => ipcRenderer.invoke('shiva:updates:branch:set', name),
    onProgress: callback => {
      const listener = (_event, payload) => callback(payload)
      ipcRenderer.on('shiva:updates:progress', listener)

      return () => ipcRenderer.removeListener('shiva:updates:progress', listener)
    }
  },
  themes: {
    fetchMarketplace: id => ipcRenderer.invoke('shiva:vscode-theme:fetch', id),
    searchMarketplace: query => ipcRenderer.invoke('shiva:vscode-theme:search', query)
  },
  // Find-in-page (Ctrl/Cmd+F): delegates to Electron's
  // webContents.findInPage on the IPC sender's window so a Cmd+F pressed
  // in a secondary session window searches THAT window, not the primary.
  // `onFoundInPage` returns the unsubscribe fn; the renderer wires it via
  // `initFindInPageListener` in store/find-in-page.ts and tears it down
  // when the FindBar unmounts.
  findInPage: (query, options) => ipcRenderer.invoke('shiva:find-in-page', query, options),
  stopFindInPage: () => ipcRenderer.invoke('shiva:stop-find-in-page'),
  onFoundInPage: callback => {
    const listener = (_event, result) => callback(result)
    ipcRenderer.on('shiva:found-in-page', listener)

    return () => ipcRenderer.removeListener('shiva:found-in-page', listener)
  },
  // Main-process `before-input-event` forwards Ctrl/Cmd+F here so renderer
  // can open the FindBar even when the GTK compositor has already grabbed
  // the chord at the windowing layer (#81727).
  onOpenFindBarRequested: callback => {
    const listener = () => callback()
    ipcRenderer.on('shiva:open-find-bar', listener)

    return () => ipcRenderer.removeListener('shiva:open-find-bar', listener)
  }
})
