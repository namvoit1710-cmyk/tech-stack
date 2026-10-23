// SA-2590 — Dashboard & Analytics. Dependency-free SVG chart primitives + panel/KPI
// shells for the Dashboard tab. No new packages (recharts is not installed);
// everything is inline SVG. Data is fetched from the analytics API (see api.ts).
import { cn } from "@ldc/ui";
import { useState, type ReactNode } from "react";

export const RANGES = [
    { value: "7d", label: "Last 7 days" },
    { value: "30d", label: "Last 30 days" },
    { value: "90d", label: "Last 90 days" },
    { value: "1y", label: "Last 12 months" },
];

/* ------------------------------------------------------------------ shells */

export function Panel({
    title,
    subtitle,
    children,
    className,
    actions,
}: {
    title: string;
    subtitle?: string;
    children: ReactNode;
    className?: string;
    actions?: ReactNode;
}) {
    return (
        <div className={cn("rounded-xl border bg-card text-card-foreground p-4", className)}>
            <div className="flex items-start justify-between gap-2">
                <div>
                    <h5 className="text-sm font-semibold">{title}</h5>
                    {subtitle && <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>}
                </div>
                {actions}
            </div>
            <div className="mt-3">{children}</div>
        </div>
    );
}

export function Kpi({
    label,
    value,
    delta,
    deltaUp,
}: {
    label: string;
    value: string;
    delta?: string;
    deltaUp?: boolean;
}) {
    return (
        <div className="rounded-xl border bg-card p-4">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
            <div className="text-2xl font-bold mt-1 leading-none">{value}</div>
            {delta && (
                <div className={cn("text-xs mt-2", deltaUp === undefined ? "text-muted-foreground" : deltaUp ? "text-emerald-500" : "text-rose-500")}>
                    {deltaUp === undefined ? "" : deltaUp ? "▲ " : "▼ "}
                    {delta}
                </div>
            )}
        </div>
    );
}

export function EmptyState({ title, hint, cta }: { title: string; hint?: string; cta?: ReactNode }) {
    return (
        <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl border border-dashed text-xl">📭</div>
            <div className="text-sm font-medium">{title}</div>
            {hint && <div className="text-xs text-muted-foreground max-w-sm">{hint}</div>}
            {cta}
        </div>
    );
}

export function Legend({ items }: { items: { color: string; label: string }[] }) {
    return (
        <div className="flex flex-wrap gap-4 mt-3 text-xs text-muted-foreground">
            {items.map((it) => (
                <span key={it.label} className="flex items-center gap-1.5">
                    <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: it.color }} />
                    {it.label}
                </span>
            ))}
        </div>
    );
}

/* ------------------------------------------------------------------ charts (SVG) */

const BLUE = "#4f8cff";
const GREEN = "#10b981";
const RED = "#f43f5e";

// SA-2625 — round a value up to a "nice" axis maximum with ~10% headroom.
function niceCeil(v: number) {
    const t = v * 1.1;
    if (t <= 1) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(t)));
    for (const s of [1, 2, 2.5, 5, 10]) if (s * mag >= t) return s * mag;
    return 10 * mag;
}

// SA-2625 — "2026-09-01" → "Sep 1"; passes anything unrecognised through.
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtDate(d?: string) {
    if (!d) return "";
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
    return m ? `${MONTHS[+m[2] - 1]} ${+m[3]}` : d;
}

// SA-2625 — shared dark hover bubble, positioned by % along the chart width.
function ChartTip({ leftPct, children }: { leftPct: number; children: ReactNode }) {
    return (
        <div
            className="pointer-events-none absolute top-1 z-10 -translate-x-1/2 whitespace-nowrap rounded-lg bg-foreground px-2.5 py-1.5 text-[11px] leading-tight text-background shadow-lg"
            style={{ left: `${Math.min(90, Math.max(10, leftPct))}%` }}
        >
            {children}
        </div>
    );
}

// SA-2625 — Runs over time: y-axis scale, x-axis dates, point markers, hover tooltip.
export function AreaLineChart({
    success,
    failed,
    labels,
    height = 200,
}: {
    success: number[];
    failed: number[];
    labels?: string[];
    height?: number;
}) {
    const [hi, setHi] = useState<number | null>(null);
    const n = success.length;
    if (n === 0) return <div className="py-12 text-center text-xs text-muted-foreground">No runs in this range</div>;
    const W = 640, H = height, padL = 42, padR = 14, padT = 14, padB = 24;
    const pw = W - padL - padR, ph = H - padT - padB;
    const top = niceCeil(Math.max(...success, ...failed, 1));
    const X = (i: number) => padL + (n <= 1 ? pw / 2 : (i / (n - 1)) * pw);
    const Y = (v: number) => padT + ph - (v / top) * ph;
    const poly = (arr: number[]) => arr.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ");
    const every = Math.max(1, Math.ceil(n / 7));
    return (
        <div className="relative">
            <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Runs over time" onMouseLeave={() => setHi(null)}>
                {[0, 0.5, 1].map((g) => {
                    const y = padT + ph - g * ph;
                    return (
                        <g key={g}>
                            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="currentColor" strokeOpacity={g === 0 ? 0.2 : 0.07} />
                            <text x={padL - 6} y={y + 3} textAnchor="end" fontSize={10} className="fill-muted-foreground">{Math.round(top * g).toLocaleString()}</text>
                        </g>
                    );
                })}
                <polygon points={`${X(0)},${padT + ph} ${poly(success)} ${X(n - 1)},${padT + ph}`} fill={GREEN} fillOpacity={0.12} />
                <polyline points={poly(success)} fill="none" stroke={GREEN} strokeWidth={2.5} />
                <polyline points={poly(failed)} fill="none" stroke={RED} strokeWidth={2} />
                {labels?.map((d, i) =>
                    i % every === 0 || i === n - 1 ? (
                        <text key={i} x={X(i)} y={H - 7} textAnchor="middle" fontSize={10} className="fill-muted-foreground">{fmtDate(d)}</text>
                    ) : null,
                )}
                {hi !== null && <line x1={X(hi)} y1={padT} x2={X(hi)} y2={padT + ph} stroke="currentColor" strokeOpacity={0.25} strokeDasharray="3 3" />}
                {success.map((v, i) => <circle key={`s${i}`} cx={X(i)} cy={Y(v)} r={hi === i ? 4 : 2.4} fill={GREEN} />)}
                {failed.map((v, i) => <circle key={`f${i}`} cx={X(i)} cy={Y(v)} r={hi === i ? 3.5 : 2} fill={RED} />)}
                {success.map((_, i) => {
                    const bw = pw / Math.max(1, n);
                    return <rect key={`h${i}`} x={X(i) - bw / 2} y={padT} width={bw} height={ph} fill="transparent" onMouseEnter={() => setHi(i)} />;
                })}
            </svg>
            {hi !== null && (
                <ChartTip leftPct={(X(hi) / W) * 100}>
                    <div className="font-semibold">{labels?.[hi] ? fmtDate(labels[hi]) : `Point ${hi + 1}`}</div>
                    <div className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: GREEN }} />Success {success[hi].toLocaleString()}</div>
                    <div className="flex items-center gap-1.5"><span className="inline-block h-2 w-2 rounded-sm" style={{ background: RED }} />Failed {failed[hi].toLocaleString()}</div>
                    <div className="opacity-70">Total {((success[hi] ?? 0) + (failed[hi] ?? 0)).toLocaleString()}</div>
                </ChartTip>
            )}
        </div>
    );
}

// SA-2625 — Usage trend: value labels, y-axis scale, x-axis bucket dates, hover tooltip.
export function MiniBars({
    values,
    labels,
    height = 200,
}: {
    values: number[];
    labels?: string[];
    height?: number;
}) {
    const [hi, setHi] = useState<number | null>(null);
    const n = values.length;
    if (n === 0) return <div className="py-12 text-center text-xs text-muted-foreground">No runs in this range</div>;
    const W = 440, H = height, padL = 30, padR = 8, padT = 18, padB = labels ? 22 : 8;
    const pw = W - padL - padR, ph = H - padT - padB;
    const top = niceCeil(Math.max(...values, 1));
    const slot = pw / n, bw = slot * 0.6;
    const X = (i: number) => padL + i * slot + (slot - bw) / 2;
    const Y = (v: number) => padT + ph - (v / top) * ph;
    const every = Math.max(1, Math.ceil(n / 8));
    return (
        <div className="relative">
            <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} role="img" aria-label="Usage trend" onMouseLeave={() => setHi(null)}>
                {[0, 0.5, 1].map((g) => {
                    const y = padT + ph - g * ph;
                    return (
                        <g key={g}>
                            <line x1={padL} y1={y} x2={W - padR} y2={y} stroke="currentColor" strokeOpacity={g === 0 ? 0.2 : 0.07} />
                            <text x={padL - 6} y={y + 3} textAnchor="end" fontSize={10} className="fill-muted-foreground">{Math.round(top * g).toLocaleString()}</text>
                        </g>
                    );
                })}
                {values.map((v, i) => {
                    const x = X(i), y = Y(v);
                    return (
                        <g key={i} onMouseEnter={() => setHi(i)}>
                            <rect x={x} y={y} width={bw} height={padT + ph - y} rx={3} fill={BLUE} fillOpacity={hi === null || hi === i ? 0.9 : 0.4} />
                            <text x={x + bw / 2} y={y - 4} textAnchor="middle" fontSize={10} fontWeight={600} className="fill-foreground">{v.toLocaleString()}</text>
                            {labels && (i % every === 0 || i === n - 1) && (
                                <text x={x + bw / 2} y={H - 6} textAnchor="middle" fontSize={9.5} className="fill-muted-foreground">{fmtDate(labels[i])}</text>
                            )}
                            <rect x={padL + i * slot} y={padT} width={slot} height={ph} fill="transparent" />
                        </g>
                    );
                })}
            </svg>
            {hi !== null && (
                <ChartTip leftPct={((X(hi) + bw / 2) / W) * 100}>
                    <div className="font-semibold">{labels?.[hi] ? fmtDate(labels[hi]) : `Bucket ${hi + 1}`}</div>
                    <div>{values[hi].toLocaleString()} runs</div>
                </ChartTip>
            )}
        </div>
    );
}

export function Donut({ success, failed, size = 150 }: { success: number; failed: number; size?: number }) {
    const total = success + failed || 1;
    const r = 60;
    const c = 2 * Math.PI * r;
    const okLen = (success / total) * c;
    const rate = ((success / total) * 100).toFixed(1);
    return (
        <svg viewBox="0 0 160 160" width={size} height={size} className="mx-auto block" role="img" aria-label="Run outcomes">
            <circle cx={80} cy={80} r={r} fill="none" stroke="currentColor" strokeOpacity={0.12} strokeWidth={22} />
            <circle cx={80} cy={80} r={r} fill="none" stroke={GREEN} strokeWidth={22} strokeDasharray={`${okLen} ${c - okLen}`} transform="rotate(-90 80 80)" />
            <circle cx={80} cy={80} r={r} fill="none" stroke={RED} strokeWidth={22} strokeDasharray={`${c - okLen} ${okLen}`} strokeDashoffset={-okLen} transform="rotate(-90 80 80)" />
            <text x={80} y={76} textAnchor="middle" className="fill-foreground" fontSize={26} fontWeight={800}>{rate}%</text>
            <text x={80} y={96} textAnchor="middle" className="fill-muted-foreground" fontSize={10}>success</text>
        </svg>
    );
}

export function RankedBars({ items }: { items: { label: string; value: number }[] }) {
    const max = Math.max(...items.map((i) => i.value), 1);
    return (
        <div className="flex flex-col gap-2.5">
            {items.map((it) => (
                <div key={it.label} className="flex items-center gap-3 text-xs">
                    <div className="w-44 shrink-0 truncate text-muted-foreground" title={it.label}>{it.label}</div>
                    <div className="flex-1 h-4 rounded bg-muted overflow-hidden">
                        <div className="h-full rounded" style={{ width: `${(it.value / max) * 100}%`, background: `linear-gradient(90deg, ${BLUE}, #38bdf8)` }} />
                    </div>
                    <div className="w-12 text-right tabular-nums font-medium">{it.value.toLocaleString()}</div>
                </div>
            ))}
        </div>
    );
}
