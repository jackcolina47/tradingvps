export const Panel = ({ title, icon: Icon, actions, children, className = "", bodyClass = "", testId, delay = 0 }) => (
  <section data-testid={testId} className={`panel rise flex flex-col min-w-0 ${className}`} style={{ animationDelay: `${delay}ms` }}>
    {title && (
      <header className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-3 border-b border-[#1E293B]">
        <div className="flex items-center gap-2 min-w-0">
          {Icon && <Icon className="h-4 w-4 text-amber-400 shrink-0" strokeWidth={2.2} />}
          <h3 className="font-display text-[13px] font-semibold uppercase tracking-[0.14em] text-slate-200 truncate">{title}</h3>
        </div>
        {actions && <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
      </header>
    )}
    <div className={`flex-1 min-h-0 ${bodyClass}`}>{children}</div>
  </section>
);

export const SideBadge = ({ side, testId }) => {
  const long = side === "long";
  return (
    <span
      data-testid={testId}
      className={`inline-flex items-center rounded-sm px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider border ${
        long ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/30" : "bg-rose-500/10 text-rose-400 border-rose-500/30"
      }`}
    >
      {long ? "Long" : "Short"}
    </span>
  );
};

export const Empty = ({ text, testId }) => (
  <div data-testid={testId} className="flex h-full min-h-[120px] items-center justify-center px-6 text-center text-sm text-slate-500">
    {text}
  </div>
);
