/**
 * Project Syndicate — Constellation Ecosystem View
 * Canvas-based force-directed graph showing agents orbiting Genesis.
 */

const ROLE_COLORS = {
    scout: '#00e5ff',
    strategist: '#ffb300',
    critic: '#ff3d3d',
    operator: '#00e676',
    genesis: '#ffffff',
};

class ConstellationView {
    constructor(canvasId, agentData) {
        this.canvas = document.getElementById(canvasId);
        if (!this.canvas) return;
        this.ctx = this.canvas.getContext('2d');
        this.agents = agentData || [];
        this.nodes = [];
        this.animId = null;
        this.init();
        this.animate();
    }

    init() {
        // Set canvas size (2x for retina)
        const rect = this.canvas.parentElement.getBoundingClientRect();
        this.canvas.width = rect.width * 2;
        this.canvas.height = rect.height * 2;
        this.ctx.scale(2, 2);
        this.dw = rect.width;
        this.dh = rect.height;

        const liveAgents = this.agents.filter(a => a.status !== 'terminated' && a.status !== 'dead');
        const cx = this.dw / 2;
        const cy = this.dh / 2;

        this.nodes = liveAgents.map((a, i) => {
            const angle = (2 * Math.PI * i) / (liveAgents.length || 1);
            const radius = 60 + Math.random() * 50;
            return {
                x: cx + Math.cos(angle) * radius,
                y: cy + Math.sin(angle) * radius,
                vx: (Math.random() - 0.5) * 0.3,
                vy: (Math.random() - 0.5) * 0.3,
                agent: a,
            };
        });
    }

    draw() {
        const ctx = this.ctx;
        const dw = this.dw;
        const dh = this.dh;
        const cx = dw / 2;
        const cy = dh / 2;
        const nodes = this.nodes;

        ctx.clearRect(0, 0, dw, dh);

        // Genesis at center
        ctx.beginPath();
        ctx.arc(cx, cy, 12, 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff20';
        ctx.fill();
        ctx.beginPath();
        ctx.arc(cx, cy, 5, 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff90';
        ctx.fill();
        ctx.font = '9px monospace';
        ctx.fillStyle = '#ffffff50';
        ctx.textAlign = 'center';
        ctx.fillText('GENESIS', cx, cy + 22);

        // Lines to Genesis
        nodes.forEach(n => {
            ctx.beginPath();
            ctx.moveTo(cx, cy);
            ctx.lineTo(n.x, n.y);
            const color = ROLE_COLORS[n.agent.role] || ROLE_COLORS.scout;
            ctx.strokeStyle = color + '18';
            ctx.lineWidth = 0.5;
            ctx.stroke();
        });

        // Dynasty connections
        nodes.forEach((n, i) => {
            nodes.forEach((m, j) => {
                if (j <= i) return;
                if (n.agent.dynasty && n.agent.dynasty === m.agent.dynasty) {
                    const dx = m.x - n.x, dy = m.y - n.y;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    if (dist < 180) {
                        ctx.beginPath();
                        ctx.moveTo(n.x, n.y);
                        ctx.lineTo(m.x, m.y);
                        ctx.strokeStyle = '#b388ff40';
                        ctx.lineWidth = 1.5;
                        ctx.stroke();
                    }
                }
            });
        });

        // Draw nodes
        nodes.forEach(n => {
            const color = ROLE_COLORS[n.agent.role] || ROLE_COLORS.scout;
            const score = n.agent.composite_score || 0;
            const r = 4 + (score / 100) * 8;

            // Glow
            ctx.beginPath();
            ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
            ctx.fillStyle = color + '10';
            ctx.fill();

            // Node
            ctx.beginPath();
            ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
            ctx.fillStyle = color + '90';
            ctx.fill();
            ctx.beginPath();
            ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
            ctx.strokeStyle = color + '50';
            ctx.lineWidth = 0.5;
            ctx.stroke();

            // Label
            ctx.font = '9px monospace';
            ctx.fillStyle = '#8892b0cc';
            ctx.textAlign = 'center';
            const label = n.agent.name.includes('-') ? n.agent.name.split('-').pop() : n.agent.name;
            ctx.fillText(label, n.x, n.y + r + 14);

            // Physics
            n.x += n.vx;
            n.y += n.vy;
            const pull = 0.003;
            n.vx += (cx - n.x) * pull * 0.1;
            n.vy += (cy - n.y) * pull * 0.1;
            n.vx *= 0.995;
            n.vy *= 0.995;

            // Repulsion
            nodes.forEach(m => {
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
    }

    animate() {
        this.draw();
        this.animId = requestAnimationFrame(() => this.animate());
    }

    destroy() {
        if (this.animId) cancelAnimationFrame(this.animId);
    }
}
