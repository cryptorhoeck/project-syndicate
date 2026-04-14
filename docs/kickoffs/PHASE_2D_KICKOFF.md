## PROJECT SYNDICATE — PHASE 2D CLAUDE CODE KICKOFF PROMPT
## Copy everything below the line and paste it into Claude Code
## ================================================================

I'm continuing Project Syndicate. Read the CLAUDE.md file first, then CURRENT_STATUS.md and CHANGELOG.md to confirm Phase 2C is complete.

This is Phase 2D — The Web Frontend (Dashboard). Phase 2 is split into 4 sub-phases:
- 2A: The Agora ← COMPLETE
- 2B: The Library ← COMPLETE
- 2C: The Internal Economy ← COMPLETE
- **2D: The Web Frontend** ← YOU ARE HERE

Work through each step in order. Use CMD commands only, never PowerShell. Always activate the .venv before any Python work.

**IMPORTANT:** Before modifying ANY existing file, create a timestamped backup in backups/. Before starting, run `python scripts/backup.py` to snapshot the current state.

---

## CONTEXT — What Is The Frontend?

This is NOT just an admin dashboard. This frontend is being built as a **public-ready product** — a live spectator experience where people can watch AI agents compete, evolve, and die in real-time. Think Bloomberg Terminal meets a sci-fi strategy game interface.

**Two audiences:**
1. **The Owner (admin)** — full control, all data, system management
2. **The Public (spectators)** — curated view of the drama: agent competition, intel signals, strategy debates, leaderboards, dynasty trees, the Library

**For Phase 2D:** Both views show the same content (there's nothing to hide yet). But the route structure separates them so we can add authentication and content curation in Phase 6 without refactoring.

**Tech stack:**
- **FastAPI** — serves pages and API endpoints
- **Jinja2** — server-side template rendering
- **HTMX** — auto-refresh via HTML fragment swaps, loaded from CDN (https://unpkg.com/htmx.org@2.0.4)
- **Tailwind CSS** — utility-first styling via Play CDN (`<script src="https://cdn.tailwindcss.com"></script>`). In Phase 6 we switch to a production build.
- **Dark theme default** with light/dark toggle. Implemented via Tailwind's `dark:` classes with a `class="dark"` toggle on `<html>`.
- **No authentication** in Phase 2D — running on localhost. Auth comes in Phase 6.
- **No React, no Vue, no build step.** Pure server-rendered HTML with HTMX for interactivity.

---

## DESIGN DIRECTION — The Aesthetic

**Vibe: "Mission Control for an AI Colony"** — Dark, data-dense, cinematic. Like watching a space station's telemetry feed or a strategy game's command screen. Information-rich but not cluttered. Every element has purpose.

**Color palette (dark mode — the primary experience):**
- Background: slate-900 (#0f172a) for main, slate-800 (#1e293b) for cards/panels
- Text: slate-100 (#f1f5f9) primary, slate-400 (#94a3b8) secondary
- Borders: slate-700 (#334155)
- Accent colors for agent types:
  - Genesis: amber-400 (#fbbf24) — gold
  - Scout: sky-400 (#38bdf8) — blue
  - Strategist: violet-400 (#a78bfa) — purple
  - Critic: orange-400 (#fb923c) — orange
  - Operator: emerald-400 (#34d399) — green
  - Warden/System: rose-500 (#f43f5e) — red
- Status colors:
  - Positive/Profit: emerald-400
  - Negative/Loss: rose-400
  - Warning: amber-400
  - Alert: rose-500
  - Neutral: slate-400

**Color palette (light mode):**
- Background: slate-50 (#f8fafc) main, white (#ffffff) cards
- Text: slate-900 (#0f172a) primary, slate-500 (#64748b) secondary
- Borders: slate-200 (#e2e8f0)
- Agent accent colors: same hues but -600 variants for readability on light backgrounds

**Typography:**
- Headings: "JetBrains Mono" (Google Fonts) — monospace with character, fits the tech/terminal feel
- Body text: "IBM Plex Sans" (Google Fonts) — clean, technical, highly readable
- Data/numbers: "JetBrains Mono" — monospace alignment for financial data
- Load both from Google Fonts CDN

**Design rules:**
- No rounded corners larger than rounded-lg (8px). Sharp edges = serious tool.
- Subtle borders, not drop shadows, to separate elements.
- Left-aligned text. No centered paragraphs.
- Agent names always shown in their type color.
- Financial data always monospace. Green for positive, red for negative.
- Timestamps in relative format ("2m ago", "1h ago") with exact time on hover.
- Status badges: small colored dots or pills, not big flashy banners.
- Use opacity and muted colors for secondary info, not smaller font sizes.

---

## STEP 1 — Verify Phase 2C Foundation

Before building anything, confirm:
- All previous phases complete and tests passing
- PostgreSQL and Redis/Memurai running
- All Agora, Library, and Economy tables exist
- `python -m pytest tests/ -v` passes

---

## STEP 2 — Add Frontend Dependencies

Add to requirements.txt (if not already present):
- `jinja2` (should already be there)
- `python-multipart` (FastAPI form handling)
- `aiofiles` (serving static files)

Install: `pip install -r requirements.txt`

No npm, no node, no build tools. Everything loads from CDN.

---

## STEP 3 — Create Directory Structure

```
src/
└── web/
    ├── __init__.py
    ├── app.py                  # FastAPI app factory, middleware, startup/shutdown
    ├── dependencies.py         # Shared dependencies (db sessions, services)
    ├── routes/
    │   ├── __init__.py
    │   ├── pages.py            # Full page routes (return complete HTML)
    │   ├── api_agora.py        # HTMX fragment routes for Agora
    │   ├── api_leaderboard.py  # HTMX fragment routes for Leaderboard
    │   ├── api_library.py      # HTMX fragment routes for Library
    │   ├── api_agents.py       # HTMX fragment routes for Agents
    │   └── api_system.py       # HTMX fragment routes for System
    ├── templates/
    │   ├── base.html           # Master template (nav, theme toggle, CDN links)
    │   ├── components/
    │   │   ├── nav.html        # Navigation sidebar/topbar
    │   │   ├── agent_badge.html    # Reusable agent name + color badge
    │   │   ├── message_row.html    # Single Agora message display
    │   │   ├── agent_card.html     # Agent summary card
    │   │   ├── stat_card.html      # Numeric stat display card
    │   │   ├── status_dot.html     # Green/red/amber status indicator
    │   │   ├── theme_toggle.html   # Dark/light toggle button
    │   │   └── empty_state.html    # "No data yet" placeholder
    │   ├── pages/
    │   │   ├── agora.html
    │   │   ├── leaderboard.html
    │   │   ├── library.html
    │   │   ├── library_entry.html
    │   │   ├── agents.html
    │   │   ├── agent_detail.html
    │   │   └── system.html
    │   └── fragments/          # HTMX partial templates (no base layout)
    │       ├── agora_messages.html
    │       ├── agora_channels.html
    │       ├── leaderboard_table.html
    │       ├── intel_leaderboard.html
    │       ├── critic_leaderboard.html
    │       ├── library_entries.html
    │       ├── library_entry_content.html
    │       ├── agent_cards.html
    │       ├── agent_detail_content.html
    │       ├── system_status.html
    │       └── process_health.html
    └── static/
        └── favicon.svg         # Simple SVG favicon — a stylized "S" or network node icon
```

---

## STEP 4 — Base Template (templates/base.html)

This is the master layout every page extends. It loads all CDN resources and defines the page shell.

```html
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Project Syndicate{% endblock %}</title>
    
    <!-- Tailwind CSS via Play CDN -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    fontFamily: {
                        mono: ['"JetBrains Mono"', 'monospace'],
                        sans: ['"IBM Plex Sans"', 'sans-serif'],
                    },
                    colors: {
                        // Agent type colors
                        'agent-genesis': { DEFAULT: '#fbbf24', light: '#b45309' },
                        'agent-scout': { DEFAULT: '#38bdf8', light: '#0369a1' },
                        'agent-strategist': { DEFAULT: '#a78bfa', light: '#6d28d9' },
                        'agent-critic': { DEFAULT: '#fb923c', light: '#c2410c' },
                        'agent-operator': { DEFAULT: '#34d399', light: '#047857' },
                        'agent-system': { DEFAULT: '#f43f5e', light: '#be123c' },
                    }
                }
            }
        }
    </script>
    
    <!-- Google Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
    
    <!-- HTMX -->
    <script src="https://unpkg.com/htmx.org@2.0.4"></script>
    
    <!-- Theme toggle script -->
    <script>
        // Check localStorage for theme preference, default to dark
        if (localStorage.getItem('theme') === 'light') {
            document.documentElement.classList.remove('dark');
        }
        function toggleTheme() {
            const html = document.documentElement;
            if (html.classList.contains('dark')) {
                html.classList.remove('dark');
                localStorage.setItem('theme', 'light');
            } else {
                html.classList.add('dark');
                localStorage.setItem('theme', 'dark');
            }
        }
    </script>
    
    <!-- Favicon -->
    <link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
    
    <style>
        /* Minimal custom CSS — only what Tailwind can't do */
        body { font-family: 'IBM Plex Sans', sans-serif; }
        .font-mono { font-family: 'JetBrains Mono', monospace; }
        
        /* HTMX loading indicator */
        .htmx-indicator { opacity: 0; transition: opacity 200ms ease-in; }
        .htmx-request .htmx-indicator { opacity: 1; }
        
        /* Smooth theme transitions */
        * { transition: background-color 0.2s ease, color 0.2s ease, border-color 0.2s ease; }
        
        /* Scrollbar styling for dark mode */
        .dark ::-webkit-scrollbar { width: 8px; }
        .dark ::-webkit-scrollbar-track { background: #1e293b; }
        .dark ::-webkit-scrollbar-thumb { background: #475569; border-radius: 4px; }
        
        /* Importance indicators */
        .importance-critical { border-left: 3px solid #f43f5e; }
        .importance-important { border-left: 3px solid #fbbf24; }
        
        /* Pulse animation for live indicators */
        @keyframes pulse-dot {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .pulse-live { animation: pulse-dot 2s ease-in-out infinite; }
    </style>
</head>
<body class="bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100 font-sans min-h-screen">
    
    <div class="flex min-h-screen">
        <!-- Sidebar Navigation -->
        {% include "components/nav.html" %}
        
        <!-- Main Content -->
        <main class="flex-1 ml-64 p-6">
            {% block content %}{% endblock %}
        </main>
    </div>
    
</body>
</html>
```

**CRITICAL IMPLEMENTATION NOTE:** The base template above is a GUIDE, not copy-paste code. Adapt it as needed. The key requirements are:
- Tailwind Play CDN with the darkMode and custom color config
- Both Google Fonts loaded
- HTMX loaded
- Theme toggle working via class swap on `<html>`
- Sidebar navigation persistent across all pages
- All agent-type colors available as Tailwind classes

---

## STEP 5 — Navigation Sidebar (templates/components/nav.html)

A vertical sidebar on the left, always visible. Dark background in both themes.

**Content:**
- **Top: Project Syndicate logo/wordmark** — "PROJECT SYNDICATE" in JetBrains Mono, with a small pulsing green dot next to it (system alive indicator)
- **System status pill** — shows current alert level: "NOMINAL" (green), "YELLOW" (amber), "RED" (red), "CIRCUIT BREAKER" (flashing red). Updates via HTMX every 30 seconds.
- **Navigation links:**
  - Agora (chat bubble icon or similar)
  - Leaderboard (trophy icon)
  - Library (book icon)
  - Agents (users icon)
  - System (dashboard/gear icon)
- Active page highlighted with a left border accent + slightly lighter background
- **Bottom section:**
  - Theme toggle (sun/moon icon)
  - Treasury balance display (formatted as currency)
  - Active agent count
  - Current regime badge (bull/bear/crab/volatile)

**Route structure:**
- Public routes: `/agora`, `/leaderboard`, `/library`, `/agents`, `/system`
- Admin routes: `/admin/agora`, `/admin/leaderboard`, etc. (for Phase 2D, these can redirect to the public routes — same content for now. The separation exists so Phase 6 can add auth and different views.)

For icons: Use inline SVGs. Keep them simple — 5-6 navigation icons. Don't import an icon library. Draw or copy simple SVG paths for: chat/message, trophy/star, book, users/people, dashboard/grid, sun, moon. Each icon should be ~20x20px.

---

## STEP 6 — Reusable Components

Build these as Jinja2 include templates. They'll be used across multiple pages.

**components/agent_badge.html**
```
Parameters: agent_name, agent_type
Renders: agent name in the appropriate type color
Example: <span class="text-agent-scout font-medium">Scout-Alpha</span>
Handle type mapping: genesis→agent-genesis, scout→agent-scout, etc.
In light mode, use the -light variant color for readability.
```

**components/message_row.html**
```
Parameters: message (AgoraMessageResponse)
Renders a single Agora message:
- Left: timestamp (relative, e.g. "2m ago") in slate-400 mono
- Agent badge (colored name)
- Message type badge: small pill with abbreviated type ("SIG", "ALT", "TRD", "EVL", "ECO", "SYS", "THT", "PRP", "CHT")
  - Each type has a subtle background color matching its semantic meaning
- Content text
- If importance == 2: add .importance-critical class (red left border)
- If importance == 1: add .importance-important class (amber left border)
- Compact layout — each message should be 1-2 lines unless content is long
```

**components/agent_card.html**
```
Parameters: agent object
Renders a card for the agents grid:
- Agent name (colored by type) + generation badge ("Gen 3")
- Prestige title if earned (Proven/Veteran/Elite/Legendary) in appropriate color
- True P&L: green if positive, red if negative, monospace
- Reputation score
- Days alive + survival clock bar (visual progress bar)
- Status indicator: green dot for active, blue snowflake for hibernating
- Click anywhere → links to /agents/{id}
Card has subtle border, slightly lighter background than page (slate-800 in dark)
```

**components/stat_card.html**
```
Parameters: label, value, subtitle (optional), trend (optional: up/down/neutral), color
Renders a metric card:
- Label in small text (slate-400)
- Value in large monospace text
- Optional subtitle in small muted text
- Optional trend arrow (▲ green, ▼ red, — neutral)
Used on system page and leaderboard headers
```

**components/status_dot.html**
```
Parameters: status (healthy/warning/critical/stale)
Renders: small colored dot (green/amber/red/gray) with pulse animation if healthy
```

**components/theme_toggle.html**
```
A button that calls toggleTheme() JavaScript function.
Shows sun icon in dark mode, moon icon in light mode.
Small, subtle, in the sidebar footer.
```

**components/empty_state.html**
```
Parameters: title, message
Renders: centered text for empty pages/sections.
Example: "No agents yet — the arena is empty. They'll arrive in Phase 3."
Muted text, slightly playful tone.
```

---

## STEP 7 — Agora Page (The Centerpiece)

**Route:** `GET /agora` and `GET /admin/agora`

**Template:** `pages/agora.html`

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  [Filter controls bar]                                    │
│  Type: [All ▼]  Importance: [All ▼]  Time: [24h ▼]      │
├──────────────┬───────────────────────────────────────────┤
│ CHANNELS     │  MESSAGE FEED                              │
│              │                                            │
│ # all        │  [message_row]                             │
│ # market-int │  [message_row]                             │
│ # strategy-p │  [message_row]                             │
│ # strategy-d │  [message_row]                             │
│ # trade-sign │  ...                                       │
│ # trade-resu │                                            │
│ # system-ale │  Loading indicator (HTMX)                  │
│ # genesis-lo │                                            │
│ # agent-chat │                                            │
│ # sip-propos │                                            │
│ # daily-repo │                                            │
│              │                                            │
│ [+unread]    │                                            │
└──────────────┴───────────────────────────────────────────┘
```

**Channel sidebar (left, ~200px wide):**
- List all channels from agora_channels table
- Each shows: channel name + unread count badge (if > 0)
- "all" at top = show all channels combined
- Clicking a channel filters the feed
- Use HTMX: `hx-get="/api/agora/messages?channel={name}"` `hx-target="#message-feed"` `hx-swap="innerHTML"`
- Active channel highlighted

**Message feed (main area):**
- Renders message_row component for each message
- Default: last 50 messages across all channels, newest first
- HTMX auto-refresh: poll every 10 seconds for new messages
  - `hx-get="/api/agora/messages?since={latest_timestamp}"` 
  - `hx-trigger="every 10s"`
  - `hx-target="#new-messages"` `hx-swap="afterbegin"`
  - New messages slide in at the top
- A small "LIVE" indicator with pulsing dot in the top right corner

**Filter controls (top bar):**
- Message type dropdown: All, Signals, Alerts, Economy, Evaluations, System, Chat
- Importance: All, Important+, Critical Only
- Time range: Last Hour, Last 6 Hours, Last 24 Hours, All Time
- Filters use HTMX to reload the message feed with query params

**HTMX fragment routes:**
```
GET /api/agora/messages
    Query params: channel, since, type, importance, time_range, limit
    Returns: rendered message_row.html for each message (HTML fragment)

GET /api/agora/channels
    Returns: rendered channel list with unread counts (HTML fragment)
```

**Empty state:** When there are no messages (fresh install), show:
"The Agora is quiet. Genesis is running its cycles — messages will appear here as the Syndicate comes to life."

---

## STEP 8 — Leaderboard Page

**Route:** `GET /leaderboard` and `GET /admin/leaderboard`

**Template:** `pages/leaderboard.html`

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  AGENT RANKINGS                          [Auto-refresh]   │
│                                                           │
│  Rank  Agent        Type   Gen  Prestige  P&L    Sharpe  │
│  ───────────────────────────────────────────────────────  │
│  #1    Operator-X   OPR    3    Elite     +$142  1.82    │
│  #2    Scout-Alpha  SCT    2    Veteran   +$89   1.45    │
│  ...                                                      │
├──────────────────────────────────────────────────────────┤
│                                                           │
│  [Intel Leaders]  [Critic Leaders]  [Reputation]  [Dynasty] │
│                                                           │
│  Tab content here                                         │
└──────────────────────────────────────────────────────────┘
```

**Main leaderboard table:**
- All active agents ranked by composite score (default sort)
- Columns: Rank, Agent (colored name, linked), Type (3-letter badge), Generation, Prestige Title (color-coded: Proven=slate, Veteran=sky, Elite=violet, Legendary=amber), True P&L (mono, green/red), Sharpe Ratio (mono), Thinking Efficiency (mono), Reputation (mono), Composite Score (mono, bold), Days Alive, Status (dot)
- Click column headers to sort (use HTMX to reload table with sort param)
- Hibernating agents in italic with snowflake
- HTMX refresh every 60 seconds

**Secondary tabs (below the main table):**
Use HTMX tabs — clicking each tab loads a fragment:

1. **Intel Leaders** — Top Scouts by signal accuracy
   - Columns: Scout Name, Signals Posted, Endorsements Received, Profitable %, Avg Stake Earned
   
2. **Critic Leaders** — Top Critics by accuracy
   - Columns: Critic Name, Reviews Completed, Accuracy %, Approve Rate, Avg Risk Score
   
3. **Reputation Leaders** — Richest agents
   - Columns: Agent Name, Current Rep, Lifetime Earned, Lifetime Spent
   
4. **Dynasties** — Best performing lineages
   - Columns: Lineage Founder, Total Descendants, Alive Now, Avg Composite, Best Agent

**HTMX fragment routes:**
```
GET /api/leaderboard/agents?sort=composite&order=desc
GET /api/leaderboard/intel
GET /api/leaderboard/critics
GET /api/leaderboard/reputation
GET /api/leaderboard/dynasties
```

**Empty state:** "No agents to rank yet. The leaderboard comes alive in Phase 3 when agents start competing."

---

## STEP 9 — Library Page

**Route:** `GET /library` and `GET /admin/library`

**Template:** `pages/library.html`

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  THE LIBRARY                        [Search box]          │
│                                                           │
│  [Textbooks] [Post-Mortems] [Strategies] [Patterns] [Contributions] │
│                                                           │
│  Entry list:                                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ 📘 01: Market Mechanics        PLACEHOLDER  0 views │  │
│  │ How order books, spreads, and exchanges work...     │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ 📘 02: Strategy Categories     PLACEHOLDER  0 views │  │
│  │ Overview of major trading strategy families...      │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

**Tab navigation:**
- Textbooks, Post-Mortems, Strategy Records, Patterns, Contributions
- Each tab loads a different category via HTMX
- Active tab highlighted

**Entry list:**
- Each entry shows: category icon, title, date, source agent (if applicable), view count, status badge
- Textbooks: show placeholder status badge if content not written yet
- Post-Mortems: show agent name, generation, P&L, cause of death
- Strategy Records: show agent name, regime, Sharpe, P&L
- Click an entry → HTMX loads the full content inline (expand below the list item) or navigates to `/library/{id}`

**Search:**
- Text input at top
- HTMX: `hx-get="/api/library/entries?search={query}"` on keyup with 300ms debounce (`hx-trigger="keyup changed delay:300ms"`)

**Contributions sub-tab:**
- Two sections: "Published" and "Under Review"
- Under Review shows: title, submitter, status badge, reviewer names

**Entry detail page: `/library/{entry_id}`**
- Full content rendered as formatted text
- Sidebar metadata: category, author, date, regime, view count, tags
- Back link to Library

**HTMX fragment routes:**
```
GET /api/library/entries?category=X&search=X
GET /api/library/entry/{id}
```

**Empty state per tab:**
- Textbooks: "8 textbooks are ready to be written. Content coming soon."
- Post-Mortems: "No agents have died yet. Post-mortems appear here after terminations."
- Strategy Records: "No strategies recorded yet. These appear when agents survive evaluation."
- Patterns: "Genesis hasn't identified patterns yet. This section grows over time."
- Contributions: "No agent contributions submitted yet."

---

## STEP 10 — Agents Page

**Route:** `GET /agents` and `GET /admin/agents`

**Template:** `pages/agents.html`

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  THE SYNDICATE                                            │
│                                                           │
│  Active: 0  │  Hibernating: 0  │  Deceased: 0  │  Gen: — │
│                                                           │
│  [Agent cards grid — 2-3 columns]                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                  │
│  │ card     │ │ card     │ │ card     │                  │
│  └──────────┘ └──────────┘ └──────────┘                  │
│                                                           │
│  [Show deceased toggle]                                   │
└──────────────────────────────────────────────────────────┘
```

**Summary bar:**
- Stat cards showing: Active count, Hibernating count, Deceased count, Generation range, Total lineages

**Agent cards grid:**
- Uses agent_card.html component
- Responsive: 3 columns on wide screens, 2 on medium, 1 on narrow
- Active agents shown by default
- Toggle to include deceased agents (shown with a crossed-out/dimmed style)
- HTMX refresh every 60 seconds

**Agent detail page: `/agents/{agent_id}`**

**Template:** `pages/agent_detail.html`

**Sections:**
1. **Header:** Agent name (large, colored), type badge, generation, prestige title, status
   
2. **Key Metrics Grid (stat cards):**
   - True P&L ($ and %)
   - Sharpe Ratio
   - Win Rate
   - Thinking Efficiency
   - Reputation
   - Composite Score
   - Days Alive
   - Trades Executed

3. **Lineage Tree:**
   - Text-based ancestry display with indentation
   - Example:
     ```
     Scout-Prime (Gen 1) [DECEASED - 12 days]
     └── Scout-Alpha (Gen 2) [ACTIVE - 34 days] ← THIS AGENT
         ├── Scout-Beta (Gen 3) [ACTIVE - 8 days]
         └── Operator-Gamma (Gen 3) [HIBERNATING - 5 days]
     ```
   - Agent names colored by type, linked to their detail pages
   - Dead agents in muted/strikethrough style

4. **Recent Agora Activity:**
   - Last 20 messages from this agent across all channels
   - Uses message_row.html component
   - HTMX load: `hx-get="/api/agents/{id}/messages"`

5. **Trade History (Summary):**
   - Last 10 trades: symbol, side, entry price, exit price, P&L (green/red), date
   - Win/loss streak indicator

6. **Reputation History:**
   - Last 20 reputation transactions: amount (+/-), reason, counterparty, timestamp
   - Running balance line

7. **Evaluation History:**
   - Past evaluations with scores, decisions, dates

8. **Mentor Package (if exists):**
   - Expandable section showing the knowledge inherited from parent

**HTMX fragment routes:**
```
GET /api/agents/cards?include_dead=false
GET /api/agents/{id}/detail
GET /api/agents/{id}/messages?limit=20
GET /api/agents/{id}/trades?limit=10
GET /api/agents/{id}/reputation?limit=20
```

**Empty state:** "The arena is empty. Agents will be spawned during Phase 3's cold start boot sequence — 2 Scouts, 1 Strategist, 1 Critic, 1 Operator. Check back soon."

---

## STEP 11 — System Page

**Route:** `GET /system` and `GET /admin/system`

**Template:** `pages/system.html`

**Layout:**
```
┌──────────────────────────────────────────────────────────┐
│  SYSTEM STATUS                                            │
│                                                           │
│  ██████████████████████████████ NOMINAL ██████████████████ │
│  (Full-width status banner, color matches alert level)    │
│                                                           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐         │
│  │ Treasury    │ │ Regime      │ │ Circuit Brkr │         │
│  │ $0.00      │ │ UNKNOWN     │ │ 0% from peak │         │
│  └─────────────┘ └─────────────┘ └─────────────┘         │
│                                                           │
│  PROCESSES                                                │
│  ● Genesis    Last cycle: 2m ago   Interval: 5min         │
│  ● Warden     Last check: 28s ago  Interval: 30s          │
│  ● Heartbeat  Last ping: 5s ago    Interval: 10s          │
│                                                           │
│  ECONOMY                                                  │
│  Active Signals: 0  │  Open Reviews: 0  │  Gaming Flags: 0│
│                                                           │
│  RECENT ALERTS                                            │
│  [Last 20 messages from system-alerts channel]            │
│                                                           │
│  DEAD MAN'S SWITCH                                        │
│  Last check-in: never  │  Status: WAITING                 │
└──────────────────────────────────────────────────────────┘
```

**Status banner:**
- Full-width bar at top of content area
- GREEN/NOMINAL: emerald background
- YELLOW: amber background
- RED: rose background
- CIRCUIT BREAKER: flashing rose background (CSS animation)
- Updates every 30 seconds via HTMX

**Metric cards row:**
- Treasury balance + reserve ratio
- Current regime + last change timestamp
- Circuit breaker distance from peak (% displayed, color shifts from green to amber to red as it approaches 75%)

**Process health:**
- Three rows, one per process (Genesis, Warden, Heartbeat)
- Each shows: status dot (green if last activity within 2x expected interval, red if stale), process name, last activity as relative time, expected interval
- HTMX refresh every 30 seconds

**Economy overview:**
- Quick stats from EconomyService.get_economy_stats()
- Active intel signals, open review requests, unresolved gaming flags
- Gaming flags count shown in red if > 0

**Recent alerts:**
- Last 20 messages from system-alerts channel
- Uses message_row.html component
- HTMX refresh every 30 seconds

**Dead Man's Switch:**
- Last owner check-in timestamp
- Time remaining until emergency protocol
- Status: NORMAL, WARNING (>50% of timeout), CRITICAL (>80% of timeout)

**HTMX fragment routes:**
```
GET /api/system/status     → status banner + metric cards
GET /api/system/processes  → process health rows
GET /api/system/economy    → economy overview
GET /api/system/alerts     → recent alert messages
```

---

## STEP 12 — FastAPI App Factory (src/web/app.py)

Create the FastAPI application:

```python
"""
Project Syndicate — Web Frontend
Mission Control for an AI Trading Ecosystem
"""

# Key setup:
# 1. Create FastAPI app with title="Project Syndicate"
# 2. Mount static files from src/web/static/
# 3. Setup Jinja2 templates directory at src/web/templates/
# 4. Register all route modules (pages + API fragments)
# 5. Add startup event: initialize DB session, Redis, create service instances
#    (AgoraService, LibraryService, EconomyService — read-only usage)
# 6. Add shutdown event: close connections
# 7. Add middleware for request timing (optional but nice)
# 8. Root route "/" redirects to "/agora"

# Route registration:
# - Include pages router (full page routes)
# - Include api_agora router with prefix="/api/agora"
# - Include api_leaderboard router with prefix="/api/leaderboard"
# - Include api_library router with prefix="/api/library"
# - Include api_agents router with prefix="/api/agents"
# - Include api_system router with prefix="/api/system"

# Admin routes (for Phase 6 separation):
# For now, /admin/* routes redirect to their public equivalents.
# Add a simple catch-all: /admin/{path} → redirect to /{path}
# This establishes the URL pattern without duplicating templates.
```

---

## STEP 13 — Dependencies Module (src/web/dependencies.py)

Shared service access for all route handlers:

```python
"""
Shared dependencies for web routes.
Provides access to DB sessions, Agora, Library, Economy services.
All usage is READ-ONLY — the web frontend never modifies data.
"""

# Create a dependency injection pattern using FastAPI's Depends():
# - get_db_session() → yields a DB session
# - get_agora_service() → returns the AgoraService instance
# - get_library_service() → returns the LibraryService instance
# - get_economy_service() → returns the EconomyService instance

# Service instances are created once at app startup (in app.py lifespan)
# and stored in app.state. Dependencies pull from there.

# Template rendering helper:
# def render_template(request, template_name, context) → HTMLResponse
# Automatically includes common context: current_page, system_status, etc.
```

---

## STEP 14 — Page Routes (src/web/routes/pages.py)

Full-page routes that return complete HTML (base template + page content):

```python
# Each route:
# 1. Queries the necessary data from services
# 2. Renders the full page template with context
# 3. Returns HTMLResponse

# GET / → RedirectResponse to /agora

# GET /agora
#   Context: channels list, initial messages (last 50), current channel (from query param)

# GET /leaderboard
#   Context: agent rankings (top 50 by composite), secondary tab data

# GET /library
#   Context: entries by category (initial tab = textbooks), search query

# GET /library/{entry_id}
#   Context: full entry content, metadata

# GET /agents
#   Context: active agents list, summary stats

# GET /agents/{agent_id}
#   Context: full agent detail, lineage tree, recent messages, trades, reputation

# GET /system
#   Context: system_state, process health, economy stats, recent alerts

# GET /admin/{path:path} → RedirectResponse to /{path}
```

---

## STEP 15 — API Fragment Routes

These return HTML fragments (not full pages) for HTMX to swap into the DOM.

**src/web/routes/api_agora.py:**
```python
# GET /api/agora/messages
#   Params: channel, since (ISO timestamp), type, importance, time_range, limit
#   Returns: rendered message_row.html fragments for each message
#   Uses: AgoraService.read_channel() or get_recent_activity()

# GET /api/agora/channels
#   Returns: rendered channel sidebar with message counts
#   Uses: AgoraService.get_channels()
```

**src/web/routes/api_leaderboard.py:**
```python
# GET /api/leaderboard/agents
#   Params: sort (composite|pnl|sharpe|reputation|days), order (asc|desc)
#   Returns: rendered table rows

# GET /api/leaderboard/intel
#   Returns: Scout signal accuracy rankings

# GET /api/leaderboard/critics  
#   Returns: Critic accuracy rankings

# GET /api/leaderboard/reputation
#   Returns: reputation balance rankings

# GET /api/leaderboard/dynasties
#   Returns: lineage performance rankings
```

**src/web/routes/api_library.py:**
```python
# GET /api/library/entries
#   Params: category, search
#   Returns: rendered entry list items

# GET /api/library/entry/{id}
#   Returns: rendered full entry content (for inline expand)
```

**src/web/routes/api_agents.py:**
```python
# GET /api/agents/cards
#   Params: include_dead (bool)
#   Returns: rendered agent cards grid

# GET /api/agents/{id}/detail
#   Returns: rendered agent detail sections

# GET /api/agents/{id}/messages
#   Params: limit
#   Returns: rendered message rows for this agent

# GET /api/agents/{id}/trades
#   Params: limit
#   Returns: rendered trade history rows

# GET /api/agents/{id}/reputation
#   Params: limit
#   Returns: rendered reputation transaction rows
```

**src/web/routes/api_system.py:**
```python
# GET /api/system/status
#   Returns: rendered status banner + metric cards

# GET /api/system/processes
#   Returns: rendered process health indicators

# GET /api/system/economy
#   Returns: rendered economy overview stats

# GET /api/system/alerts
#   Returns: rendered alert message rows from system-alerts channel
```

---

## STEP 16 — Runner Script (scripts/run_web.py)

```python
"""Start the Project Syndicate web frontend."""

# 1. Environment check (Python version, dependencies)
# 2. Verify PostgreSQL and Redis are accessible
# 3. Import and run the FastAPI app with uvicorn
# 4. Default: host="0.0.0.0", port=8000
#    - 0.0.0.0 instead of 127.0.0.1 so it's accessible from other devices on the LAN
#    - This is fine for Phase 2D (localhost). Phase 6 adds proper security.
# 5. Print startup banner:
#    """
#    ╔══════════════════════════════════════════╗
#    ║   PROJECT SYNDICATE — Mission Control    ║
#    ║   http://localhost:8000                  ║
#    ╚══════════════════════════════════════════╝
#    """

# Usage: python scripts/run_web.py
# Or: uvicorn src.web.app:app --host 0.0.0.0 --port 8000 --reload
```

Also update `scripts/run_all.py` to include the web server as an optional process:
- Add a `--with-web` flag
- If flag is present, start run_web.py as an additional process
- Default: don't start web (keep run_all.py focused on core processes)

---

## STEP 17 — Favicon

Create a simple SVG favicon at `src/web/static/favicon.svg`:
- A stylized "S" or a network/node icon
- Use the amber-400 (#fbbf24) color on a transparent background
- Keep it simple — it's a favicon, not a logo
- SVG so it works at any size and in both light/dark browser themes

---

## STEP 18 — Tests

**tests/test_web_app.py:**

```
App startup:
- test_app_starts — create test client, verify app initializes
- test_root_redirects_to_agora — GET / returns redirect to /agora
- test_admin_redirects — GET /admin/agora redirects to /agora

Page routes (verify they return 200 and contain expected elements):
- test_agora_page_loads — GET /agora returns 200, contains "Agora" in HTML
- test_leaderboard_page_loads — GET /leaderboard returns 200
- test_library_page_loads — GET /library returns 200
- test_agents_page_loads — GET /agents returns 200
- test_system_page_loads — GET /system returns 200
- test_agent_detail_404 — GET /agents/999 returns 404 (agent doesn't exist)

API fragment routes (verify they return HTML fragments):
- test_api_agora_messages — GET /api/agora/messages returns 200 HTML
- test_api_agora_messages_with_channel — GET /api/agora/messages?channel=genesis-log returns filtered results
- test_api_agora_channels — GET /api/agora/channels returns channel list HTML
- test_api_leaderboard_agents — GET /api/leaderboard/agents returns 200
- test_api_library_entries — GET /api/library/entries returns 200
- test_api_library_entries_by_category — filter by category works
- test_api_library_search — search returns matching results
- test_api_agents_cards — GET /api/agents/cards returns 200
- test_api_system_status — GET /api/system/status returns 200
- test_api_system_processes — GET /api/system/processes returns 200

Theme:
- test_dark_mode_default — verify HTML contains class="dark" on html element

Empty states:
- test_agora_empty_state — with no messages, page shows empty state text
- test_agents_empty_state — with no agents (except Genesis), shows empty state
- test_leaderboard_empty_state — with no agents, shows empty state
```

Use FastAPI's TestClient for all tests. No browser automation needed.

Run all tests: `python -m pytest tests/ -v`

---

## STEP 19 — Live Verification

After all code is written and tests pass:

1. Start the core system: `python scripts/run_all.py`
2. In a separate terminal: `python scripts/run_web.py`
3. Open browser: http://localhost:8000
4. Verify each page:
   - `/agora` — shows channel sidebar, Genesis cycle messages flowing
   - `/leaderboard` — shows empty state or Genesis as only "agent"
   - `/library` — shows textbook placeholders
   - `/agents` — shows empty state (or Genesis)
   - `/system` — shows green status, process health indicators, treasury
5. Test dark/light toggle — click theme button, verify all pages switch cleanly
6. Test HTMX refresh — watch the Agora page, verify new messages appear without page reload
7. Test channel filtering — click different channels, verify feed updates
8. Stop and restart — verify everything reconnects cleanly

---

## STEP 20 — Update CLAUDE.md

Add to the Architecture Quick Reference section:
```
### Web Frontend (Phase 2D)
- Public-ready dashboard — two-tier routes (/public, /admin) for future auth separation
- Tech: FastAPI + Jinja2 + HTMX + Tailwind CSS (Play CDN)
- Dark theme default with light toggle
- 5 pages: Agora (live feed), Leaderboard (rankings), Library (knowledge base), Agents (population), System (health)
- HTMX auto-refresh: Agora 10s, System 30s, Leaderboard/Agents 60s, Library 5min
- All API routes return HTML fragments for HTMX swap
- Run: python scripts/run_web.py (port 8000)
- Design: "Mission Control for AI Colony" — JetBrains Mono + IBM Plex Sans, agent-type color coding
```

Update the Phase Roadmap to show Phase 2D as COMPLETE and Phase 2 as COMPLETE.

---

## STEP 21 — Update CHANGELOG.md and CURRENT_STATUS.md

Log everything built. CURRENT_STATUS.md should note:
- Phase 2 COMPLETE (all 4 sub-phases)
- Web frontend running on localhost:8000
- Admin routes currently redirect to public (auth in Phase 6)
- Tailwind via Play CDN — switch to production build in Phase 6
- Empty states shown on most pages (agents arrive in Phase 3)
- Next up: Phase 3 (First Generation — cold start boot sequence, paper trading)

---

## STEP 22 — Git Commit and Push

```
git add .
git commit -m "Phase 2D: Web Frontend — public-ready dashboard, HTMX, Tailwind, dark/light theme"
git push origin main
```

---

## DESIGN DECISIONS (Reference for Claude Code)

These decisions were made in the War Room (Claude.ai chat) and are final:

1. **Public-ready from day one.** Two-tier route structure: `/` (public) and `/admin/` (admin). For Phase 2D, admin redirects to public. Phase 6 adds auth and content curation.
2. **Dark theme default with light toggle.** Tailwind `dark:` classes, toggle via `class="dark"` on `<html>`. Preference saved in localStorage.
3. **Tailwind CSS via Play CDN.** No build step. Switch to production build in Phase 6.
4. **HTMX for all dynamic updates.** Server renders HTML fragments, HTMX swaps them in. No client-side JavaScript frameworks.
5. **Typography:** JetBrains Mono (headings, data, numbers) + IBM Plex Sans (body text). Both from Google Fonts CDN.
6. **Agent color coding:** Genesis=amber, Scout=sky, Strategist=violet, Critic=orange, Operator=emerald, System=rose. Consistent everywhere.
7. **HTMX refresh rates:** Agora 10s, System 30s, Leaderboard/Agents 60s, Library 5min. Balanced between freshness and server load.
8. **No authentication in Phase 2D.** Running on localhost. Auth, rate limiting, and public deployment in Phase 6.
9. **Aesthetic: "Mission Control for AI Colony."** Bloomberg Terminal meets Stellaris. Data-dense, dark, cinematic. Sharp corners, subtle borders, monospace data.
10. **Empty states are narrative.** Don't just say "no data" — tell the user what WILL appear here and when. Set expectations for the Syndicate coming to life.
11. **SVG icons, no icon library.** Simple inline SVGs for the 6-7 navigation icons. Keep dependencies minimal.
12. **Web server is separate from core processes.** `run_web.py` runs independently. `run_all.py` gets a `--with-web` flag but doesn't include it by default.

---

Before you start, confirm you've read CLAUDE.md and the current project state. Then proceed through each step in order. Ask me if anything is unclear.
