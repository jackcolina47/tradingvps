export const RANGES = [{ key: 7, label: "7 días" }, { key: 30, label: "30 días" }, { key: 90, label: "90 días" }, { key: 0, label: "Todo" }];
export const GROUPS = [{ key: "day", label: "Día" }, { key: "week", label: "Semana" }, { key: "month", label: "Mes" }];

const pad = (n) => String(n).padStart(2, "0");
const localDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function weekStart(day) {
  const d = new Date(`${day}T00:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return localDay(d);
}

export function filterRange(daily, days) {
  if (!days) return daily;
  const from = new Date();
  from.setDate(from.getDate() - (days - 1));
  const min = localDay(from);
  return daily.filter((d) => d.day >= min);
}

export function groupBy(daily, mode) {
  const keyOf = mode === "week" ? weekStart : mode === "month" ? (day) => `${day.slice(0, 7)}-01` : (day) => day;
  const acc = new Map();
  for (const d of daily) {
    const k = keyOf(d.day);
    const a = acc.get(k) || { day: k, pnl: 0, trades: 0, wins: 0, days: 0 };
    a.pnl += d.pnl; a.trades += d.trades; a.wins += d.wins; a.days += 1;
    acc.set(k, a);
  }
  let cum = 0;
  return [...acc.values()].sort((a, b) => a.day.localeCompare(b.day)).map((a) => {
    a.pnl = Math.round(a.pnl * 100) / 100;
    cum = Math.round((cum + a.pnl) * 100) / 100;
    return { ...a, cum };
  });
}

export const fmtBucket = (day, mode) => (mode === "month" ? `${day.slice(5, 7)}/${day.slice(2, 4)}` : `${day.slice(8, 10)}/${day.slice(5, 7)}`);
export const bucketTitle = (day, mode) => (mode === "week" ? `Semana del ${day}` : mode === "month" ? `Mes ${day.slice(0, 7)}` : day);
export const unitLabel = (mode, n) => (mode === "week" ? (n === 1 ? "semana" : "semanas") : mode === "month" ? (n === 1 ? "mes" : "meses") : n === 1 ? "día" : "días");
