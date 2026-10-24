import React, { useEffect, useMemo, useState } from 'react';
import { Radar, Loader2, AlertTriangle, Info, ArrowUpDown } from 'lucide-react';
import type { Lead, LeadsResponse } from '../types';
import { fetchLeads } from '../services/api';
import { fmtInt } from '../lib/capital';

interface LeadsTabProps {
  onSelectStock: (ticker: string) => void;
  onOpenTab: (tab: 'deals' | 'rights') => void;
}

const panel: React.CSSProperties = {
  background: 'rgba(15, 23, 42, 0.65)',
  backdropFilter: 'blur(16px)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  borderRadius: '16px',
  padding: '1.25rem',
};

type Kind = 'all' | 'ma' | 'capital';
type Tracked = 'all' | 'untracked' | 'tracked';
type SortKey = 'total' | 'score_ma' | 'score_capital' | 'last' | 'momentum' | 'r3M';

const PERIODS: { id: string; label: string; days: number | null }[] = [
  { id: '30', label: 'Signal in last 30 days', days: 30 },
  { id: '90', label: 'Last 90 days', days: 90 },
  { id: '365', label: 'Last 12 months', days: 365 },
  { id: 'all', label: 'Whole screen period', days: null },
];

const GROUP_COLOR: Record<string, string> = { ma: '#f472b6', capital: '#a78bfa', support: '#64748b' };
const pct = (x: number | null | undefined) => (x == null ? '—' : `${x >= 0 ? '+' : ''}${(x * 100).toFixed(0)}%`);
const tone = (x: number | null | undefined) =>
  x == null ? 'var(--text-secondary)' : x > 0.005 ? '#34d399' : x < -0.005 ? '#f87171' : 'var(--text-secondary)';
const scoreColor = (s: number) => (s >= 75 ? '#34d399' : s >= 50 ? '#facc15' : s >= 25 ? '#fb923c' : '#f87171');

export const LeadsTab: React.FC<LeadsTabProps> = ({ onSelectStock, onOpenTab }) => {
  const [data, setData] = useState<LeadsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<Kind>('ma');
  const [tracked, setTracked] = useState<Tracked>('all');
  const [period, setPeriod] = useState('90');
  const [required, setRequired] = useState<string[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>('total');
  const [sortDesc, setSortDesc] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    fetchLeads()
      .then(setData)
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const signals = data?.signals ?? {};
  const leads = data?.leads ?? [];
  const dealSignals = Object.entries(signals).filter(([, s]) => s.group !== 'support');

  const filtered = useMemo(() => {
    const days = PERIODS.find((p) => p.id === period)?.days ?? null;
    const ref = data?.screen?.date_to ? new Date(data.screen.date_to) : new Date();
    const cutoff = days ? new Date(ref.getTime() - days * 86400000).toISOString().slice(0, 10) : '';
    const q = query.trim().toUpperCase();
    const val = (l: Lead): number => {
      switch (sortKey) {
        case 'total':
          return kind === 'capital' ? l.score_capital : kind === 'ma' ? l.score_ma : l.score_ma + l.score_capital;
        case 'last':
          return l.last ? Date.parse(l.last) : 0;
        case 'momentum':
          return l.momentum?.score ?? Number.NEGATIVE_INFINITY;
        case 'r3M':
          return l.momentum?.r3M ?? Number.NEGATIVE_INFINITY;
        default:
          return l[sortKey];
      }
    };
    return leads
      .filter(
        (l) =>
          (kind === 'all' || (kind === 'ma' ? l.score_ma > 0 : l.score_capital > 0)) &&
          (tracked === 'all' || (tracked === 'tracked' ? l.tracked.length > 0 : l.tracked.length === 0)) &&
          (!cutoff || (l.last ?? '') >= cutoff) &&
          (!q || l.ticker.includes(q)) &&
          required.every((s) => l.signals.includes(s)),
      )
      .sort((a, b) => (sortDesc ? val(b) - val(a) : val(a) - val(b)));
  }, [leads, data, query, kind, tracked, period, required, sortKey, sortDesc]);

  const kpis = useMemo(
    () => ({
      total: leads.length,
      ma: leads.filter((l) => l.score_ma > 0).length,
      capital: leads.filter((l) => l.score_capital > 0).length,
      tracked: leads.filter((l) => l.tracked.length > 0).length,
    }),
    [leads],
  );

  const sortBy = (key: SortKey) => {
    if (key === sortKey) setSortDesc(!sortDesc);
    else {
      setSortKey(key);
      setSortDesc(true);
    }
  };
  const toggleSignal = (s: string) => setRequired((r) => (r.includes(s) ? r.filter((x) => x !== s) : [...r, s]));

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '4rem', color: 'var(--text-secondary)' }}>
        <Loader2 size={32} className="spinning" style={{ color: '#38bdf8' }} />
        <p>Loading leads…</p>
      </div>
    );
  }
  if (error || !data?.screen) {
    return (
      <div className="error-banner">
        <AlertTriangle size={40} />
        <h2>No screen yet</h2>
        <p>{error ?? 'Leads come from the market-wide title screen. The worker builds it at 19:00 on trading days, or run:'}</p>
        <code>uv run idx screen --from 2025-01-01</code>
      </div>
    );
  }

  const th = (label: string, key?: SortKey, align: 'left' | 'right' = 'right', title?: string) => (
    <th
      onClick={key ? () => sortBy(key) : undefined}
      style={{ textAlign: align, cursor: key ? 'pointer' : 'default', whiteSpace: 'nowrap', color: key === sortKey ? '#38bdf8' : undefined }}
      title={title ?? (key ? 'Sort' : undefined)}
    >
      {label}
      {key && <ArrowUpDown size={11} style={{ marginLeft: 3, opacity: key === sortKey ? 1 : 0.35 }} />}
    </th>
  );
  const btn = (active: boolean): React.CSSProperties => ({
    padding: '0.4rem 0.8rem',
    cursor: 'pointer',
    borderRadius: '10px',
    border: active ? '1px solid rgba(56, 189, 248, 0.5)' : '1px solid rgba(255,255,255,0.1)',
    background: active ? 'rgba(56, 189, 248, 0.12)' : 'transparent',
    color: active ? '#38bdf8' : '#cbd5e1',
    fontWeight: 600,
    fontSize: '0.8rem',
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {/* Banner */}
      <div style={{ ...panel, padding: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
            <Radar size={28} style={{ color: '#f472b6' }} />
            <h2 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 700 }}>Leads</h2>
          </div>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.9rem', maxWidth: 760 }}>
            Every stock whose IDX filings show a takeover, merger, business-change or capital-raising signal — found automatically, not yet
            judged. Read a lead's filings first; the ones that hold up go to the{' '}
            <a onClick={() => onOpenTab('deals')} style={{ cursor: 'pointer', color: '#38bdf8' }}>Deal Tracker</a> or{' '}
            <a onClick={() => onOpenTab('rights')} style={{ cursor: 'pointer', color: '#38bdf8' }}>Rights Issues</a>.
          </p>
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', textAlign: 'right' }}>
          Filings {data.screen.date_from} → {data.screen.date_to} ({fmtInt(data.screen.filings_scanned)} scanned)
          <br />
          Prices as of {data.prices_as_of ?? '—'}
        </div>
      </div>

      {/* KPI tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '0.75rem' }}>
        {[
          { label: 'Leads', value: kpis.total, color: '#f8fafc' },
          { label: 'M&A / backdoor signals', value: kpis.ma, color: GROUP_COLOR.ma },
          { label: 'Capital-raise signals', value: kpis.capital, color: GROUP_COLOR.capital },
          { label: 'Already tracked', value: kpis.tracked, color: '#34d399' },
        ].map((k) => (
          <div key={k.label} style={{ ...panel, padding: '1rem' }}>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{k.label}</div>
            <div style={{ fontSize: '1.5rem', fontWeight: 800, color: k.color, fontVariantNumeric: 'tabular-nums' }}>{fmtInt(k.value)}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div style={{ ...panel, display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '0.9rem 1.25rem' }}>
        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
          {(
            [
              ['ma', 'M&A / backdoor'],
              ['capital', 'Capital raising'],
              ['all', 'All'],
            ] as [Kind, string][]
          ).map(([k, label]) => (
            <button key={k} onClick={() => setKind(k)} style={btn(kind === k)}>{label}</button>
          ))}
          <select className="form-control" style={{ maxWidth: '200px' }} value={period} onChange={(e) => setPeriod(e.target.value)}>
            {PERIODS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
          <select className="form-control" style={{ maxWidth: '170px' }} value={tracked} onChange={(e) => setTracked(e.target.value as Tracked)}>
            <option value="all">Tracked and new</option>
            <option value="untracked">Not tracked yet</option>
            <option value="tracked">Tracked only</option>
          </select>
          <input className="form-control" style={{ maxWidth: '160px', paddingLeft: '1rem' }} placeholder="Ticker…" value={query} onChange={(e) => setQuery(e.target.value)} />
          <span style={{ marginLeft: 'auto', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{fmtInt(filtered.length)} leads</span>
        </div>
        <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', flexWrap: 'wrap', fontSize: '0.75rem' }}>
          <span style={{ color: 'var(--text-secondary)', marginRight: 4 }}>Must have:</span>
          {dealSignals.map(([key, s]) => {
            const on = required.includes(key);
            return (
              <button
                key={key}
                onClick={() => toggleSignal(key)}
                title={s.label}
                style={{
                  padding: '0.15rem 0.5rem',
                  borderRadius: 6,
                  cursor: 'pointer',
                  fontWeight: 700,
                  fontSize: '0.7rem',
                  border: `1px solid ${GROUP_COLOR[s.group]}${on ? '' : '55'}`,
                  background: on ? `${GROUP_COLOR[s.group]}33` : 'transparent',
                  color: GROUP_COLOR[s.group],
                }}
              >
                {key}
              </button>
            );
          })}
          {required.length > 0 && (
            <button onClick={() => setRequired([])} style={{ ...btn(false), padding: '0.15rem 0.5rem', fontSize: '0.7rem' }}>clear</button>
          )}
        </div>
      </div>

      {/* Table */}
      <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
        <div className="table-container" style={{ marginTop: 0, maxHeight: '720px', overflowY: 'auto' }}>
          <table style={{ fontSize: '0.82rem' }}>
            <thead>
              <tr>
                {th('#', undefined, 'left')}
                {th('Ticker', undefined, 'left')}
                {th('Signal score', 'total', 'right', 'Sum of signal weights in the selected group (sort)')}
                {th('M&A', 'score_ma')}
                {th('Capital', 'score_capital')}
                {th('Signals', undefined, 'left')}
                {th('Stage (furthest)', undefined, 'left')}
                {th('Last signal', 'last')}
                {th('Momentum', 'momentum', 'right', 'Price-momentum percentile among all IDX stocks (sort)')}
                {th('3M', 'r3M')}
                {th('From high')}
                {th('Value 20d (Rp bn)')}
              </tr>
            </thead>
            <tbody>
              {filtered.map((l, i) => {
                const m = l.momentum;
                const isOpen = open === l.ticker;
                return (
                  <React.Fragment key={l.ticker}>
                    <tr onClick={() => setOpen(isOpen ? null : l.ticker)} style={{ cursor: 'pointer', background: isOpen ? 'rgba(56, 189, 248, 0.08)' : undefined }}>
                      <td className="numeric">{i + 1}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            onSelectStock(l.ticker);
                          }}
                          className="ticker-badge"
                          style={{ cursor: 'pointer', border: 'none' }}
                          title="Open in Stock Terminal"
                        >
                          {l.ticker}
                        </button>
                        {l.tracked.map((t) => (
                          <span
                            key={t}
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenTab(t === 'deal' ? 'deals' : 'rights');
                            }}
                            title={t === 'deal' ? 'In the Deal Tracker' : 'In Rights Issues → Tracked raises'}
                            style={{ marginLeft: 6, fontSize: '0.62rem', fontWeight: 700, color: '#34d399', border: '1px solid #34d39955', borderRadius: 5, padding: '0 0.3rem', cursor: 'pointer' }}
                          >
                            {t === 'deal' ? 'DEAL' : 'RIGHTS'}
                          </span>
                        ))}
                      </td>
                      <td className="numeric" style={{ textAlign: 'right', fontWeight: 700 }}>
                        {kind === 'capital' ? l.score_capital : kind === 'ma' ? l.score_ma : l.score_ma + l.score_capital}
                      </td>
                      <td className="numeric" style={{ textAlign: 'right', color: l.score_ma ? GROUP_COLOR.ma : 'var(--text-secondary)' }}>{l.score_ma || '—'}</td>
                      <td className="numeric" style={{ textAlign: 'right', color: l.score_capital ? GROUP_COLOR.capital : 'var(--text-secondary)' }}>{l.score_capital || '—'}</td>
                      <td>
                        <div style={{ display: 'flex', gap: '0.2rem', flexWrap: 'wrap', maxWidth: 240 }}>
                          {l.signals.map((s) => (
                            <span
                              key={s}
                              title={signals[s]?.label ?? s}
                              style={{ fontSize: '0.62rem', fontWeight: 700, padding: '0 0.3rem', borderRadius: 5, color: GROUP_COLOR[signals[s]?.group ?? 'support'], border: `1px solid ${GROUP_COLOR[signals[s]?.group ?? 'support']}55` }}
                            >
                              {s}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td style={{ fontSize: '0.78rem' }}>{l.stage ?? '—'}</td>
                      <td className="numeric" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>{l.last ?? '—'}</td>
                      {m ? (
                        <>
                          <td style={{ textAlign: 'right' }}>
                            <span className="numeric" style={{ fontWeight: 700, color: scoreColor(m.score) }}>{m.score.toFixed(0)}</span>
                          </td>
                          <td className="numeric" style={{ textAlign: 'right', color: tone(m.r3M) }}>{pct(m.r3M)}</td>
                          <td className="numeric" style={{ textAlign: 'right', color: tone(m.from_high) }}>{pct(m.from_high)}</td>
                          <td className="numeric" style={{ textAlign: 'right' }}>{(m.avg_value_20d / 1e9).toFixed(1)}</td>
                        </>
                      ) : (
                        <td colSpan={4} style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>No recent price (suspended or not trading)</td>
                      )}
                    </tr>
                    {isOpen && (
                      <tr>
                        <td colSpan={12} style={{ background: 'rgba(15, 23, 42, 0.5)', fontSize: '0.78rem', lineHeight: 1.5 }}>
                          <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>
                            {l.filings ?? '—'} filings, first signal {l.first ?? '—'}. Key titles:
                          </div>
                          <ul style={{ margin: 0, paddingLeft: '1.1rem' }}>
                            {l.key_titles.map(([d, keys, title], j) => (
                              <li key={j}>
                                <span className="numeric">{d}</span> <span style={{ color: '#a78bfa' }}>[{keys}]</span> {title}
                              </li>
                            ))}
                          </ul>
                          <div style={{ marginTop: 6, color: 'var(--text-secondary)' }}>
                            Read the filings: <code>idx dossier {l.ticker}</code>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={12} style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-secondary)' }}>No leads match these filters.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
        <Info size={14} style={{ flexShrink: 0, marginTop: '2px' }} />
        <span>
          Leads come from filing titles only (rebuilt every trading day at 19:00), so a high score means "worth reading", not a confirmed deal — many
          business-change signals are routine KBLI updates or finished deals. Signal score = sum of signal weights; stage = the furthest deal step seen.
          Momentum is the same percentile as on the Momentum tab. Click a row for its key filings.
        </span>
      </div>
    </div>
  );
};
