/* Cron, read as information rather than syntax.
 *
 * Two jobs: say when a pass runs in words a person can read, and place its runs on a 24-hour
 * track so an agent's card shows its working day at a glance. Deliberately forgiving — an
 * expression we can't parse falls back to showing the raw text, never an error. */

const DOW = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

const pad = (n: number | string) => String(n).padStart(2, "0");

/** Expand one cron field ("*", "*\/2", "3", "1,4", "9-11") into concrete values. */
function expand(field: string, max: number): number[] | "all" {
  if (field === "*") return "all";
  const out = new Set<number>();
  for (const part of field.split(",")) {
    const step = part.match(/^\*\/(\d+)$/);
    if (step) {
      const n = Number(step[1]);
      if (n > 0) for (let i = 0; i < max; i += n) out.add(i);
      continue;
    }
    const range = part.match(/^(\d+)-(\d+)(?:\/(\d+))?$/);
    if (range) {
      const [, a, b, s] = range;
      const stride = Number(s || 1);
      for (let i = Number(a); i <= Number(b) && i < max; i += stride) out.add(i);
      continue;
    }
    if (/^\d+$/.test(part)) out.add(Number(part) % max);
  }
  return out.size ? [...out].sort((a, b) => a - b) : [];
}

export interface Shift {
  /** "band" = runs right through the day; "ticks" = specific hours; "unknown" = unparsed. */
  kind: "band" | "ticks" | "unknown";
  hours: number[];
  /** True when it only runs on certain days — drawn hollow, since most days it's quiet. */
  weekly: boolean;
}

export function shiftOf(expr: string): Shift {
  const parts = (expr || "").trim().split(/\s+/);
  if (parts.length !== 5) return { kind: "unknown", hours: [], weekly: false };
  const [, hour, dom, , dow] = parts;
  const weekly = dow !== "*" || dom !== "*";
  const hours = expand(hour, 24);
  if (hours === "all") return { kind: "band", hours: [], weekly };
  if (!hours.length) return { kind: "unknown", hours: [], weekly };
  return { kind: "ticks", hours, weekly };
}

/** When this runs, in words: "03:00 daily", "08:00 on Mondays", "every hour". */
export function describeCron(expr: string): string {
  const parts = (expr || "").trim().split(/\s+/);
  if (parts.length !== 5) return expr || "not scheduled";
  const [min, hour, dom, , dow] = parts;
  const minute = /^\d+$/.test(min) ? pad(min) : min;

  if (hour === "*") return min === "0" ? "Every hour" : `Every hour at :${minute}`;
  const hours = expand(hour, 24);
  if (hours === "all" || !hours.length) return expr;
  const time =
    hours.length === 1
      ? `${pad(hours[0])}:${minute}`
      : `${hours.map((h) => pad(h)).join(", ")} at :${minute}`;

  if (dow !== "*") {
    const days = expand(dow, 7);
    const named =
      days === "all" || !days.length ? dow : days.map((d) => DOW[d % 7]).join(" and ");
    return `${time} on ${named}s`;
  }
  if (dom !== "*") return `${time} on day ${dom} of the month`;
  return `${time} daily`;
}

/** "in 4h", "in 12m", "now" — a next-run time said the way a person would. */
export function untilText(iso: string | null): string {
  if (!iso) return "not scheduled";
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return iso;
  if (ms <= 0) return "any moment";
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `in ${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `in ${hours}h`;
  return `in ${Math.round(hours / 24)}d`;
}

/** "4h ago", "just now", "never" — for last-run times. */
export function agoText(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms)) return iso;
  const mins = Math.round(ms / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
