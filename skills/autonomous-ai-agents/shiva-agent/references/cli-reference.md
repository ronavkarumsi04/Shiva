# Shiva CLI Reference

Live sources when anything looks stale: `shiva --help`, `shiva <command> --help`,
https://shiva-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
shiva [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
shiva chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
shiva setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
shiva model                Interactive model/provider picker
shiva fallback [add|remove|list]  Fallback provider chain
shiva config [show|edit|get|set|unset|path|env-path|check|migrate]
shiva login / logout       OAuth sign-in / clear stored auth
shiva doctor [--fix]       Check dependencies and config
shiva status [--all]       Component status
```

### Tools & Skills

```
shiva tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

shiva skills list|browse|search QUERY|inspect ID
shiva skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
shiva skills config        Enable/disable skills per platform
shiva skills check|update|uninstall|publish PATH
shiva skills tap add REPO  Add a GitHub repo as a skill source
shiva bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
shiva mcp add NAME (--url or --command) | remove | list | test NAME
shiva mcp catalog | install NAME     Curated catalog install
shiva mcp configure NAME             Toggle tool selection
shiva mcp serve                      Run Shiva as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
shiva gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `shiva photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://shiva-agent.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
shiva sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
shiva cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
shiva webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
shiva profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
shiva profile rename A B | alias NAME | export NAME | import FILE
```

### Credentials & Pools

```
shiva auth                 Interactive credential manager
shiva auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
shiva auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
shiva desktop / gui        Native desktop app
shiva dashboard            Web admin panel + embedded chat (--stop / --status)
shiva proxy                OpenAI-compatible local proxy backed by an OAuth provider
shiva portal               Quick setup / sign in via Nous Portal
shiva kanban <verb>        Multi-agent work-queue board
shiva project              Named multi-folder workspaces
shiva skin list|use|set    Switch/tweak skins (see references/themes.md)
shiva pets <verb>          Pet mascots (see references/petdex.md)
shiva memory setup|status|off|reset   Memory provider
shiva secrets bitwarden|onepassword   External secret stores
shiva moa                  Mixture-of-Agents slots
shiva hooks / security / backup / import / checkpoints / console
shiva logs [-f] [errors]   View agent/error logs
shiva send                 One-off message through a gateway platform
shiva pairing / plugins / insights / journey / computer-use
shiva acp                  ACP server (IDE integration)
shiva completion bash|zsh|fish
shiva update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `shiva photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `shiva config edit` · [Configuration docs](https://shiva-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `shiva tools list` · [Tools reference](https://shiva-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `shiva skills browse` · [Skills catalog](https://shiva-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `shiva model` · [Providers guide](https://shiva-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `shiva config env-path` · [Env vars reference](https://shiva-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.shiva/logs/gateway.log` (or `shiva logs`) |
| Sessions | `shiva sessions browse` (reads state.db) |
