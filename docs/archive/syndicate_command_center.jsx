import { useState, useEffect, useRef, useCallback, useMemo } from "react";

const COLORS = {
  bg: "#080c18",
  bgPanel: "#0d1225",
  bgCard: "#111832",
  border: "#1a2444",
  borderGlow: "#2a3a6a",
  cyan: "#00e5ff",
  cyanDim: "#00849480",
  amber: "#ffb300",
  amberDim: "#b8860b60",
  red: "#ff3d3d",
  redDim: "#ff3d3d50",
  green: "#00e676",
  greenDim: "#00e67650",
  purple: "#b388ff",
  purpleDim: "#b388ff50",
  silver: "#8892b0",
  textPrimary: "#ccd6f6",
  textSecondary: "#8892b0",
  textDim: "#4a5580",
};

const ROLES = {
  scout: { color: COLORS.cyan, icon: "◎", label: "SCOUT" },
  strategist: { color: COLORS.amber, icon: "◈", label: "STRATEGIST" },
  critic: { color: COLORS.red, icon: "◆", label: "CRITIC" },
  operator: { color: COLORS.green, icon: "▣", label: "OPERATOR" },
};

const PRESTIGE = {
  Unproven: { stars: 0, color: COLORS.textDim },
  Proven: { stars: 1, color: COLORS.silver },
  Veteran: { stars: 2, color: COLORS.amber },
  Elite: { stars: 3, color: COLORS.cyan },
  Legendary: { stars: 4, color: COLORS.purple },
};

const STATUSES = ["HUNTING", "MONITORING", "ANALYZING", "EXECUTING", "REFLECTING", "HIBERNATING"];

function generateSparkline(length = 20, trend = 0) {
  const data = [];
  let val = 50 + Math.random() * 20;
  for (let i = 0; i < length; i++) {
    val += (Math.random() - 0.48 + trend * 0.02) * 8;
    val = Math.max(10, Math.min(90, val));
    data.push(val);
  }
  return data;
}

function HexAvatar({ id, size = 48, role, isActive, isDying, isDead }) {
  const hash = useMemo(() => {
    let h = 0;
    const s = String(id) + role;
    for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }, [id, role]);

  const roleColor = ROLES[role]?.color || COLORS.cyan;
  const segments = useMemo(() => {
    const segs = [];
    for (let i = 0; i < 6; i++) {
      const bit = (hash >> (i * 4)) & 0xf;
      const opacity = 0.2 + (bit / 15) * 0.8;
      segs.push(opacity);
    }
    return segs;
  }, [hash]);

  const r = size / 2;
  const cr = r * 0.75;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ filter: isDead ? "grayscale(1) opacity(0.4)" : "none" }}>
      {segments.map((op, i) => {
        const a1 = (Math.PI / 3) * i - Math.PI / 2;
        const a2 = (Math.PI / 3) * (i + 1) - Math.PI / 2;
        const x1 = r + cr * Math.cos(a1);
        const y1 = r + cr * Math.sin(a1);
        const x2 = r + cr * Math.cos(a2);
        const y2 = r + cr * Math.sin(a2);
        return <path key={i} d={`M${r},${r}L${x1},${y1}L${x2},${y2}Z`} fill={roleColor} opacity={op} />;
      })}
      <circle cx={r} cy={r} r={cr} fill="none" stroke={roleColor} strokeWidth="1" opacity={0.6} />
      {isActive && !isDead && (
        <circle cx={r} cy={r} r={cr + 3} fill="none" stroke={roleColor} strokeWidth="1.5" opacity={0.3}>
          <animate attributeName="r" values={`${cr + 2};${cr + 6};${cr + 2}`} dur="2s" repeatCount="indefinite" />
          <animate attributeName="opacity" values="0.4;0.1;0.4" dur="2s" repeatCount="indefinite" />
        </circle>
      )}
      {isDying && !isDead && (
        <>
          <line x1={r - cr * 0.6} y1={r - cr * 0.3} x2={r + cr * 0.2} y2={r + cr * 0.5} stroke={COLORS.red} strokeWidth="1" opacity="0.6" />
          <line x1={r + cr * 0.3} y1={r - cr * 0.5} x2={r - cr * 0.1} y2={r + cr * 0.4} stroke={COLORS.red} strokeWidth="0.8" opacity="0.4" />
        </>
      )}
    </svg>
  );
}

function Sparkline({ data, width = 120, height = 28, color = COLORS.cyan }) {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data.map((v, i) => `${(i / (data.length - 1)) * width},${height - ((v - min) / range) * height}`).join(" ");
  const trend = data[data.length - 1] > data[0];
  const c = trend ? COLORS.green : COLORS.red;
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <polyline points={points} fill="none" stroke={c} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" opacity="0.8" />
    </svg>
  );
}

function SurvivalBar({ daysRemaining, maxDays = 21 }) {
  const pct = Math.max(0, Math.min(100, (daysRemaining / maxDays) * 100));
  const barColor = pct > 50 ? COLORS.green : pct > 25 ? COLORS.amber : COLORS.red;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
      <div style={{ flex: 1, height: 4, background: COLORS.border, borderRadius: 2, overflow: "hidden" }}>
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: barColor,
            borderRadius: 2,
            transition: "width 1s ease",
            boxShadow: `0 0 6px ${barColor}40`,
          }}
        />
      </div>
      <span style={{ color: barColor, minWidth: 50, textAlign: "right", fontFamily: "'JetBrains Mono', monospace" }}>
        {daysRemaining.toFixed(1)}d
      </span>
    </div>
  );
}

function StatusDots({ status }) {
  const isHibernating = status === "HIBERNATING";
  const dotColor = isHibernating ? COLORS.textDim : COLORS.cyan;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
      {[0, 1, 2, 3, 4].map((i) => (
        <div
          key={i}
          style={{
            width: 5,
            height: 5,
            borderRadius: "50%",
            background: i === 2 ? dotColor : "transparent",
            border: `1px solid ${dotColor}60`,
            animation: !isHibernating ? `pulse-dot 1.5s ease-in-out ${i * 0.2}s infinite` : "none",
            opacity: isHibernating ? 0.3 : 1,
          }}
        />
      ))}
    </div>
  );
}

function AgentCard({ agent, index }) {
  const role = ROLES[agent.role] || ROLES.scout;
  const prestige = PRESTIGE[agent.prestige] || PRESTIGE.Unproven;
  const isDying = agent.survivalDays < 3;
  const isDead = agent.status === "TERMINATED";
  const isActive = !isDead && agent.status !== "HIBERNATING";
  const isHibernating = agent.status === "HIBERNATING";

  const borderColor = isDead ? COLORS.textDim : isDying ? COLORS.red : isHibernating ? COLORS.textDim : role.color;
  const glowOpacity = isDead ? 0 : isDying ? 0.4 : isActive ? 0.2 : 0.05;

  return (
    <div
      style={{
        background: isDead ? "#0a0d18" : COLORS.bgCard,
        border: `1px solid ${borderColor}${isDead ? "30" : "60"}`,
        borderRadius: 12,
        padding: "14px 16px",
        position: "relative",
        overflow: "hidden",
        opacity: isDead ? 0.5 : isHibernating ? 0.6 : 1,
        animation: `card-in 0.5s ease ${index * 0.1}s both`,
        filter: isDead ? "grayscale(0.8)" : "none",
        transition: "all 0.3s ease",
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: -1,
          borderRadius: 12,
          boxShadow: `inset 0 0 30px ${borderColor}${Math.round(glowOpacity * 255).toString(16).padStart(2, "0")}`,
          pointerEvents: "none",
        }}
      />

      {isDead && (
        <div style={{
          position: "absolute", top: "50%", left: "50%", transform: "translate(-50%, -50%) rotate(-15deg)",
          fontSize: 11, fontWeight: 700, color: COLORS.red, opacity: 0.6, letterSpacing: 4,
          border: `2px solid ${COLORS.red}60`, padding: "4px 16px", borderRadius: 4,
          fontFamily: "'JetBrains Mono', monospace", zIndex: 2,
        }}>TERMINATED</div>
      )}

      <div style={{ display: "flex", gap: 12, marginBottom: 10 }}>
        <HexAvatar id={agent.id} size={48} role={agent.role} isActive={isActive} isDying={isDying} isDead={isDead} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
            <span style={{ fontSize: 10, color: role.color, fontFamily: "'JetBrains Mono', monospace", opacity: 0.7 }}>
              {role.icon} {role.label}
            </span>
            <span style={{ fontSize: 10, color: COLORS.textDim, marginLeft: "auto" }}>Gen {agent.generation}</span>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: COLORS.textPrimary, marginBottom: 3, letterSpacing: 0.5 }}>
            {agent.name}
          </div>
          <div style={{ fontSize: 10, color: COLORS.purple, opacity: 0.7 }}>{agent.dynasty}</div>
        </div>
      </div>

      <SurvivalBar daysRemaining={agent.survivalDays} />

      <div style={{ display: "flex", alignItems: "center", gap: 4, margin: "8px 0 6px" }}>
        <span style={{ fontSize: 10, color: prestige.color, fontFamily: "'JetBrains Mono', monospace" }}>
          {agent.prestige}
        </span>
        <span style={{ fontSize: 10, color: prestige.color }}>
          {"★".repeat(prestige.stars)}{"☆".repeat(4 - prestige.stars)}
        </span>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 8 }}>
        {[
          { label: "TRUE P&L", value: `${agent.pnl >= 0 ? "+" : ""}$${agent.pnl.toFixed(2)}`, color: agent.pnl >= 0 ? COLORS.green : COLORS.red },
          { label: "SHARPE", value: agent.sharpe.toFixed(2), color: agent.sharpe > 1 ? COLORS.green : agent.sharpe > 0 ? COLORS.amber : COLORS.red },
          { label: "EFFICIENCY", value: `${agent.efficiency.toFixed(1)}x`, color: agent.efficiency > 1 ? COLORS.green : COLORS.red },
        ].map((m) => (
          <div key={m.label}>
            <div style={{ fontSize: 9, color: COLORS.textDim, marginBottom: 2, letterSpacing: 1 }}>{m.label}</div>
            <div style={{ fontSize: 13, fontWeight: 600, color: m.color, fontFamily: "'JetBrains Mono', monospace" }}>{m.value}</div>
          </div>
        ))}
      </div>

      <Sparkline data={agent.sparkline} color={role.color} />

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 10, color: isHibernating ? COLORS.textDim : role.color, fontFamily: "'JetBrains Mono', monospace" }}>
            {agent.status}
          </span>
          <StatusDots status={agent.status} />
        </div>
        <span style={{ fontSize: 9, color: COLORS.textDim, fontFamily: "'JetBrains Mono', monospace" }}>
          {agent.model} · ${agent.cycleCost.toFixed(4)}
        </span>
      </div>
    </div>
  );
}

const EVENT_TEMPLATES = [
  { type: "trade", icon: "⚡", color: COLORS.green, msg: (a) => `${a} executed BUY 0.00${Math.floor(Math.random() * 9 + 1)} BTC/USDT @ $${(67400 + Math.random() * 600).toFixed(0)}` },
  { type: "trade_sell", icon: "⚡", color: COLORS.red, msg: (a) => `${a} executed SELL 0.00${Math.floor(Math.random() * 5 + 1)} ETH/USDT @ $${(3340 + Math.random() * 60).toFixed(0)}` },
  { type: "intel", icon: "◎", color: COLORS.cyan, msg: (a) => `${a} broadcast: ${["BTC momentum shift detected", "ETH volume anomaly on 15m", "SOL/USDT consolidation breaking", "AVAX showing accumulation pattern", "XRP divergence on RSI"][Math.floor(Math.random() * 5)]}` },
  { type: "plan", icon: "◈", color: COLORS.amber, msg: (a) => `${a} submitted Plan #${Math.floor(Math.random() * 90 + 10)} — ${["momentum long BTC", "mean reversion ETH", "breakout entry SOL", "range scalp AVAX"][Math.floor(Math.random() * 4)]}` },
  { type: "reject", icon: "◆", color: COLORS.red, msg: (a) => `${a} rejected Plan #${Math.floor(Math.random() * 90 + 10)} — ${["risk/reward insufficient", "position too large", "correlated exposure", "regime mismatch"][Math.floor(Math.random() * 4)]}` },
  { type: "eval", icon: "◇", color: COLORS.silver, msg: () => `Genesis evaluation cycle complete — ${Math.floor(Math.random() * 3 + 3)} agents reviewed` },
  { type: "hibernate", icon: "◌", color: COLORS.textDim, msg: (a) => `${a} entered hibernation — budget conservation` },
  { type: "reflect", icon: "◐", color: COLORS.purple, msg: (a) => `${a} reflection cycle — ${["promoted 2 memories", "curated long-term knowledge", "updated trust scores"][Math.floor(Math.random() * 3)]}` },
];

function LiveFeed({ events }) {
  const feedRef = useRef(null);
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = 0;
  }, [events.length]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ fontSize: 11, color: COLORS.textDim, letterSpacing: 2, marginBottom: 10, display: "flex", alignItems: "center", gap: 8 }}>
        LIVE FEED
        <div style={{ width: 6, height: 6, borderRadius: "50%", background: COLORS.green, animation: "blink 1.5s infinite" }} />
      </div>
      <div ref={feedRef} style={{ flex: 1, overflow: "auto", display: "flex", flexDirection: "column", gap: 2 }}>
        {events.map((e, i) => (
          <div
            key={e.id}
            style={{
              padding: "7px 10px",
              borderRadius: 6,
              fontSize: 12,
              color: COLORS.textSecondary,
              background: i === 0 ? `${e.color}08` : "transparent",
              borderLeft: `2px solid ${e.color}${i === 0 ? "80" : "30"}`,
              animation: i === 0 ? "feed-in 0.4s ease" : "none",
              opacity: Math.max(0.3, 1 - i * 0.06),
              fontFamily: "'JetBrains Mono', monospace",
              lineHeight: 1.5,
            }}
          >
            <span style={{ color: COLORS.textDim, marginRight: 8 }}>{e.time}</span>
            <span style={{ color: e.color, marginRight: 6 }}>{e.icon}</span>
            {e.text}
          </div>
        ))}
      </div>
    </div>
  );
}

function Leaderboard({ agents }) {
  const sorted = [...agents].filter((a) => a.status !== "TERMINATED").sort((a, b) => b.composite - a.composite);
  return (
    <div>
      <div style={{ fontSize: 11, color: COLORS.textDim, letterSpacing: 2, marginBottom: 10 }}>LEADERBOARD</div>
      {sorted.map((a, i) => {
        const role = ROLES[a.role] || ROLES.scout;
        return (
          <div
            key={a.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "6px 10px",
              borderRadius: 6,
              marginBottom: 2,
              background: i === 0 ? `${COLORS.amber}10` : "transparent",
              borderLeft: i === 0 ? `2px solid ${COLORS.amber}` : "2px solid transparent",
            }}
          >
            <span style={{
              fontSize: 12, fontWeight: 700, color: i === 0 ? COLORS.amber : i < 3 ? COLORS.textPrimary : COLORS.textDim,
              minWidth: 18, textAlign: "right", fontFamily: "'JetBrains Mono', monospace",
            }}>
              {i === 0 ? "♛" : `${i + 1}.`}
            </span>
            <span style={{ fontSize: 10, color: role.color }}>{role.icon}</span>
            <span style={{ fontSize: 12, color: COLORS.textPrimary, flex: 1, fontWeight: i < 3 ? 600 : 400 }}>{a.name}</span>
            <span style={{ fontSize: 10, color: a.rankDelta > 0 ? COLORS.green : a.rankDelta < 0 ? COLORS.red : COLORS.textDim, fontFamily: "'JetBrains Mono', monospace" }}>
              {a.rankDelta > 0 ? `▲${a.rankDelta}` : a.rankDelta < 0 ? `▼${Math.abs(a.rankDelta)}` : "─"}
            </span>
            <span style={{ fontSize: 11, color: COLORS.textSecondary, minWidth: 36, textAlign: "right", fontFamily: "'JetBrains Mono', monospace" }}>
              {a.composite.toFixed(2)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ConstellationView({ agents }) {
  const canvasRef = useRef(null);
  const animRef = useRef(null);
  const nodesRef = useRef([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.offsetWidth * 2;
    const h = canvas.height = canvas.offsetHeight * 2;
    ctx.scale(2, 2);
    const dw = w / 2, dh = h / 2;

    const liveAgents = agents.filter(a => a.status !== "TERMINATED");
    if (nodesRef.current.length !== liveAgents.length) {
      nodesRef.current = liveAgents.map((a, i) => {
        const angle = (2 * Math.PI * i) / liveAgents.length;
        const radius = 60 + Math.random() * 50;
        return {
          x: dw / 2 + Math.cos(angle) * radius,
          y: dh / 2 + Math.sin(angle) * radius,
          vx: (Math.random() - 0.5) * 0.3,
          vy: (Math.random() - 0.5) * 0.3,
          agent: a,
        };
      });
    } else {
      nodesRef.current.forEach((n, i) => { n.agent = liveAgents[i] || n.agent; });
    }

    function draw() {
      ctx.clearRect(0, 0, dw, dh);
      const nodes = nodesRef.current;
      const cx = dw / 2, cy = dh / 2;

      ctx.beginPath();
      ctx.arc(cx, cy, 12, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff20";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(cx, cy, 5, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff90";
      ctx.fill();
      ctx.font = "9px monospace";
      ctx.fillStyle = "#ffffff50";
      ctx.textAlign = "center";
      ctx.fillText("GENESIS", cx, cy + 22);

      nodes.forEach((n) => {
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(n.x, n.y);
        const role = ROLES[n.agent.role] || ROLES.scout;
        ctx.strokeStyle = `${role.color}18`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      });

      nodes.forEach((n, i) => {
        nodes.forEach((m, j) => {
          if (j <= i) return;
          if (n.agent.dynasty === m.agent.dynasty && n.agent.dynasty) {
            const dx = m.x - n.x, dy = m.y - n.y;
            const dist = Math.sqrt(dx * dx + dy * dy);
            if (dist < 180) {
              ctx.beginPath();
              ctx.moveTo(n.x, n.y);
              ctx.lineTo(m.x, m.y);
              ctx.strokeStyle = `${COLORS.purple}40`;
              ctx.lineWidth = 1.5;
              ctx.stroke();
            }
          }
        });
      });

      nodes.forEach((n) => {
        const role = ROLES[n.agent.role] || ROLES.scout;
        const r = 4 + (n.agent.composite / 100) * 8;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
        ctx.fillStyle = `${role.color}10`;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = `${role.color}90`;
        ctx.fill();
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.strokeStyle = `${role.color}50`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
        ctx.font = "9px monospace";
        ctx.fillStyle = `${COLORS.textSecondary}cc`;
        ctx.textAlign = "center";
        ctx.fillText(n.agent.name.split("-")[1] || n.agent.name, n.x, n.y + r + 14);

        n.x += n.vx;
        n.y += n.vy;
        const pull = 0.003;
        n.vx += (cx - n.x) * pull * 0.1;
        n.vy += (cy - n.y) * pull * 0.1;
        n.vx *= 0.995;
        n.vy *= 0.995;
        nodes.forEach((m) => {
          if (m === n) return;
          const dx = n.x - m.x, dy = n.y - m.y;
          const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
          if (dist < 50) {
            const force = 0.5 / dist;
            n.vx += (dx / dist) * force;
            n.vy += (dy / dist) * force;
          }
        });
      });
      animRef.current = requestAnimationFrame(draw);
    }
    draw();
    return () => cancelAnimationFrame(animRef.current);
  }, [agents]);

  return <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />;
}

function EventBanner({ event, onDismiss }) {
  if (!event) return null;
  return (
    <div style={{
      position: "fixed", top: 0, left: 0, right: 0, zIndex: 100,
      background: `linear-gradient(90deg, ${event.color}20, ${event.color}08)`,
      borderBottom: `1px solid ${event.color}60`,
      padding: "10px 20px",
      display: "flex", alignItems: "center", justifyContent: "center", gap: 12,
      animation: "banner-in 0.5s ease",
      backdropFilter: "blur(10px)",
    }}>
      <span style={{ fontSize: 18 }}>{event.icon}</span>
      <span style={{ fontSize: 13, color: COLORS.textPrimary, fontWeight: 600, fontFamily: "'JetBrains Mono', monospace", letterSpacing: 0.5 }}>
        {event.text}
      </span>
    </div>
  );
}

const INITIAL_AGENTS = [
  { id: 1, name: "SCOUT-ALPHA", role: "scout", generation: 1, dynasty: "House of Genesis", prestige: "Veteran", survivalDays: 11.2, pnl: 12.40, sharpe: 1.82, efficiency: 3.1, composite: 72, status: "HUNTING", model: "Haiku", cycleCost: 0.0008, sparkline: generateSparkline(20, 1), rankDelta: 2 },
  { id: 2, name: "SCOUT-BETA", role: "scout", generation: 1, dynasty: "House of Genesis", prestige: "Proven", survivalDays: 8.5, pnl: -2.10, sharpe: 0.34, efficiency: 0.8, composite: 38, status: "ANALYZING", model: "Haiku", cycleCost: 0.0006, sparkline: generateSparkline(20, -0.5), rankDelta: -1 },
  { id: 3, name: "STRAT-PRIME", role: "strategist", generation: 1, dynasty: "House of Genesis", prestige: "Veteran", survivalDays: 13.8, pnl: 8.70, sharpe: 1.45, efficiency: 2.4, composite: 65, status: "ANALYZING", model: "Sonnet", cycleCost: 0.0031, sparkline: generateSparkline(20, 0.8), rankDelta: 0 },
  { id: 4, name: "CRITIC-ONE", role: "critic", generation: 1, dynasty: "House of Genesis", prestige: "Elite", survivalDays: 14.0, pnl: 0, sharpe: 0, efficiency: 4.2, composite: 58, status: "MONITORING", model: "Sonnet", cycleCost: 0.0028, sparkline: generateSparkline(20, 0), rankDelta: 1 },
  { id: 5, name: "OPERATOR-3", role: "operator", generation: 2, dynasty: "House of Op-1", prestige: "Veteran", survivalDays: 9.3, pnl: 22.80, sharpe: 2.10, efficiency: 3.8, composite: 81, status: "EXECUTING", model: "Sonnet", cycleCost: 0.0035, sparkline: generateSparkline(20, 1.5), rankDelta: 0 },
  { id: 6, name: "OPERATOR-5", role: "operator", generation: 2, dynasty: "House of Op-1", prestige: "Proven", survivalDays: 5.1, pnl: -4.30, sharpe: -0.22, efficiency: 0.5, composite: 28, status: "MONITORING", model: "Haiku", cycleCost: 0.0007, sparkline: generateSparkline(20, -1), rankDelta: -2 },
  { id: 7, name: "STRAT-2", role: "strategist", generation: 2, dynasty: "House of Genesis", prestige: "Unproven", survivalDays: 2.1, pnl: -1.50, sharpe: 0.15, efficiency: 0.3, composite: 18, status: "HIBERNATING", model: "Haiku", cycleCost: 0.0004, sparkline: generateSparkline(20, -0.8), rankDelta: 0 },
  { id: 8, name: "SCOUT-GAMMA", role: "scout", generation: 1, dynasty: "House of Genesis", prestige: "Unproven", survivalDays: 0, pnl: -6.20, sharpe: -0.80, efficiency: 0.2, composite: 8, status: "TERMINATED", model: "—", cycleCost: 0, sparkline: generateSparkline(20, -2), rankDelta: 0 },
];

const AGENT_NAMES = INITIAL_AGENTS.filter((a) => a.status !== "TERMINATED").map((a) => a.name);

export default function SyndicateCommandCenter() {
  const [agents] = useState(INITIAL_AGENTS);
  const [events, setEvents] = useState([]);
  const [banner, setBanner] = useState(null);
  const [uptime, setUptime] = useState(0);
  const eventIdRef = useRef(0);

  const addEvent = useCallback(() => {
    const template = EVENT_TEMPLATES[Math.floor(Math.random() * EVENT_TEMPLATES.length)];
    const agentName = AGENT_NAMES[Math.floor(Math.random() * AGENT_NAMES.length)];
    const text = template.msg(agentName);
    const id = eventIdRef.current++;
    const secs = Math.floor(Math.random() * 30);
    const newEvent = { id, icon: template.icon, color: template.color, text, time: `${secs}s`, type: template.type };

    setEvents((prev) => [newEvent, ...prev].slice(0, 50));

    if (template.type === "trade" || template.type === "trade_sell") {
      if (Math.random() < 0.15) {
        setBanner({ icon: "⚡", color: COLORS.green, text: `TRADE EXECUTED — ${text}` });
        setTimeout(() => setBanner(null), 5000);
      }
    }
  }, []);

  useEffect(() => {
    for (let i = 0; i < 12; i++) setTimeout(() => addEvent(), i * 100);
    const interval = setInterval(addEvent, 3000 + Math.random() * 4000);
    const uptimeInterval = setInterval(() => setUptime((u) => u + 1), 1000);
    return () => { clearInterval(interval); clearInterval(uptimeInterval); };
  }, [addEvent]);

  const formatUptime = (s) => {
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    const m = Math.floor((s % 3600) / 60);
    return `${d}d ${h}h ${m}m`;
  };

  const treasury = 487.20;
  const alertLevel = "GREEN";
  const regime = "TRENDING BULL";
  const haiku_pct = 87;
  const cache_hit = 72;
  const saved = 1.84;

  return (
    <div style={{ background: COLORS.bg, color: COLORS.textPrimary, minHeight: "100vh", fontFamily: "'Inter', -apple-system, sans-serif" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: ${COLORS.border}; border-radius: 2px; }
        @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
        @keyframes pulse-dot { 0%,100%{transform:scale(1);opacity:0.5} 50%{transform:scale(1.8);opacity:1} }
        @keyframes card-in { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
        @keyframes feed-in { from{opacity:0;transform:translateX(-10px)} to{opacity:1;transform:translateX(0)} }
        @keyframes banner-in { from{transform:translateY(-100%)} to{transform:translateY(0)} }
        @keyframes scan { 0%{background-position:200% 0} 100%{background-position:-200% 0} }
      `}</style>

      <EventBanner event={banner} />

      <div style={{
        background: COLORS.bgPanel,
        borderBottom: `1px solid ${COLORS.border}`,
        padding: "0 20px",
        height: 48,
        display: "flex",
        alignItems: "center",
        gap: 20,
        position: "sticky",
        top: 0,
        zIndex: 50,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 15, fontWeight: 700, color: COLORS.textPrimary, letterSpacing: 2 }}>
            PROJECT SYNDICATE
          </span>
          <div style={{
            background: `${COLORS.green}20`,
            border: `1px solid ${COLORS.green}40`,
            borderRadius: 4,
            padding: "2px 8px",
            fontSize: 10,
            color: COLORS.green,
            fontWeight: 600,
            fontFamily: "'JetBrains Mono', monospace",
            display: "flex",
            alignItems: "center",
            gap: 4,
          }}>
            <div style={{ width: 5, height: 5, borderRadius: "50%", background: COLORS.green, animation: "blink 1.5s infinite" }} />
            LIVE
          </div>
        </div>

        <div style={{ flex: 1 }} />

        {[
          { label: "TREASURY", value: `$${treasury.toFixed(2)}`, color: COLORS.green },
          { label: "ALERT", value: alertLevel, color: COLORS.green },
          { label: "REGIME", value: regime, color: COLORS.cyan },
          { label: "UPTIME", value: formatUptime(uptime + 367200), color: COLORS.textSecondary },
          { label: "AGENTS", value: `${agents.filter((a) => a.status !== "TERMINATED").length}/${agents.length}`, color: COLORS.textSecondary },
        ].map((s) => (
          <div key={s.label} style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
            <span style={{ fontSize: 9, color: COLORS.textDim, letterSpacing: 1.5, marginBottom: 1 }}>{s.label}</span>
            <span style={{ fontSize: 12, fontWeight: 600, color: s.color, fontFamily: "'JetBrains Mono', monospace" }}>{s.value}</span>
          </div>
        ))}
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 320px",
        gridTemplateRows: "auto auto auto",
        gap: 0,
        minHeight: "calc(100vh - 48px)",
      }}>
        <div style={{ padding: 16, borderRight: `1px solid ${COLORS.border}` }}>
          <div style={{ fontSize: 11, color: COLORS.textDim, letterSpacing: 2, marginBottom: 12 }}>AGENTS</div>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
            gap: 12,
          }}>
            {agents.map((a, i) => <AgentCard key={a.id} agent={a} index={i} />)}
          </div>
        </div>

        <div style={{ padding: 16, borderBottom: `1px solid ${COLORS.border}`, maxHeight: 500, display: "flex", flexDirection: "column" }}>
          <LiveFeed events={events} />
        </div>

        <div style={{ padding: 16, borderRight: `1px solid ${COLORS.border}`, borderTop: `1px solid ${COLORS.border}` }}>
          <div style={{ fontSize: 11, color: COLORS.textDim, letterSpacing: 2, marginBottom: 12 }}>ECOSYSTEM</div>
          <div style={{ height: 280, background: `${COLORS.bgPanel}`, borderRadius: 8, border: `1px solid ${COLORS.border}`, overflow: "hidden" }}>
            <ConstellationView agents={agents} />
          </div>
        </div>

        <div style={{ padding: 16, borderTop: `1px solid ${COLORS.border}`, display: "flex", flexDirection: "column", gap: 20 }}>
          <Leaderboard agents={agents} />

          <div>
            <div style={{ fontSize: 11, color: COLORS.textDim, letterSpacing: 2, marginBottom: 10 }}>SYSTEM</div>
            {[
              { label: "Market Regime", value: regime, color: COLORS.cyan },
              { label: "Next Evaluation", value: "1h 47m", color: COLORS.amber },
              { label: "Haiku Routing", value: `${haiku_pct}%`, color: COLORS.green },
              { label: "Cache Hit Rate", value: `${cache_hit}%`, color: COLORS.green },
              { label: "Saved Today", value: `$${saved.toFixed(2)}`, color: COLORS.green },
              { label: "Avg Cost/Cycle", value: "$0.0012", color: COLORS.textSecondary },
            ].map((s) => (
              <div key={s.label} style={{
                display: "flex", justifyContent: "space-between", alignItems: "center",
                padding: "5px 0", borderBottom: `1px solid ${COLORS.border}40`,
              }}>
                <span style={{ fontSize: 11, color: COLORS.textSecondary }}>{s.label}</span>
                <span style={{ fontSize: 11, fontWeight: 600, color: s.color, fontFamily: "'JetBrains Mono', monospace" }}>{s.value}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
