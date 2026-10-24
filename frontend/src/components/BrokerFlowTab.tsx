import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Users, Loader2, AlertTriangle, Info, ArrowUpDown, Download, KeyRound } from 'lucide-react';
import type { BrokerFlowFetchResult, BrokerFlowResponse, BrokerFlowStatus, BrokerPosition, BrokerStatus } from '../types';
import { fetchBrokerPositions, fetchBrokerFlowStatus, runBrokerFlowFetch } from '../services/api';
import { fmtInt } from '../lib/capital';

interface BrokerFlowTabProps {
  onSelectStock: (ticker: string) => void;
}

const panel: React.CSSProperties = {
  background: 'rgba(15, 23, 42, 0.65)',
  backdropFilter: 'blur(16px)',
  border: '1px solid rgba(255, 255, 255, 0.08)',
  borderRadius: '16px',
  padding: '1.25rem',
};

const STATUS: Record<BrokerStatus, { label: string; color: string; hint: string }> = {
  fresh_accum: { label: 'Fresh accum', color: '#34d399', hint: 'Was flat or net short, now net long' },
  avg_down: { label: 'Avg down', color: '#38bdf8', hint: 'Added to a long position below its average cost' },
  avg_up: { label: 'Avg up', color: '#a78bfa', hint: 'Added to a long position at or above its average cost' },
  trim: { label: 'Trim', color: '#facc15', hint: 'Sold part of a long position at or above its average cost' },
  cut_loss: { label: 'Cut loss', color: '#fb923c', hint: 'Sold part of a long position below its average cost' },
  exit: { label: 'Exit', color: '#f87171', hint: 'Was long, now flat or net short' },
  distribute: { label: 'Distribute', color: '#f43f5e', hint: 'Net seller in the window and still selling' },
  cover: { label: 'Cover', color: '#2dd4bf', hint: 'Net seller in the window, now buying back' },
  hold: { label: 'Hold', color: '#94a3b8', hint: 'Position changed by less than 2%' },
  inactive: { label: 'Inactive', color: '#64748b', hint: 'Flat throughout (round trips only)' },
};

type SortKey = 'position_value' | 'floating_pl' | 'floating_pct' | 'realized_pl' | 'total_pl' | 'recent_net_shares' | 'share_of_volume';
type Side = 'all' | 'long' | 'short';

const isoDaysAgo = (days: number) => new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
const fmtRpShort = (n: number) => {
  const a = Math.abs(n);
  const s = n < 0 ? '−' : '';
  if (a >= 1e12) return `${s}Rp${(a / 1e12).toFixed(2)}T`;
  if (a >= 1e9) return `${s}Rp${(a / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${s}Rp${(a / 1e6).toFixed(0)}M`;
  return `${s}Rp${fmtInt(a)}`;
};
const tone = (x: number | null | undefined) =>
  x == null ? 'var(--text-secondary)' : x > 0 ? '#34d399' : x < 0 ? '#f87171' : 'var(--text-secondary)';

export const BrokerFlowTab: React.FC<BrokerFlowTabProps> = ({ onSelectStock }) => {
  const [status, setStatus] = useState<BrokerFlowStatus | null>(null);
  const [ticker, setTicker] = useState('');
  const [from, setFrom] = useState(isoDaysAgo(90));
  const [to, setTo] = useState(isoDaysAgo(0));
  const [recent, setRecent] = useState(5);
  const [data, setData] = useState<BrokerFlowResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [fetchResult, setFetchResult] = useState<BrokerFlowFetchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyStatus, setOnlyStatus] = useState<BrokerStatus | null>(null);
  const [side, setSide] = useState<Side>('all');
  const [sortKey, setSortKey] = useState<SortKey>('position_value');
  const [sortDesc, setSortDesc] = useState(true);

  const refreshStatus = useCallback(() => {
    fetchBrokerFlowStatus().then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(refreshStatus, [refreshStatus]);

  const load = useCallback(
    (code = ticker) => {
      const t = code.trim().toUpperCase();
      if (!t) return;
      setLoading(true);
      setError(null);
      setOnlyStatus(null);
      fetchBrokerPositions(t, from, to, recent)
        .then(setData)
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false));
    },
    [ticker, from, to, recent],
  );

  const runFetch = () => {
    if (!data) return;
    setFetching(true);
    setError(null);
    runBrokerFlowFetch(data.stock, from, to)
      .then((res) => {
        setFetchResult(res);
        refreshStatus();
        load(data.stock);
      })
      .catch((err) => setError(err.message))
      .finally(() => setFetching(false));
  };

  const rows = useMemo(() => {
    const list = (data?.brokers ?? []).filter(
      (b) =>
        (!onlyStatus || b.status === onlyStatus) &&
        (side === 'all' || (side === 'long' ? b.position > 0 : b.position < 0)),
    );
    const v = (b: BrokerPosition) => (b[sortKey] ?? Number.NEGATIVE_INFINITY) as number;
    return [...list].sort((a, b) => (sortDesc ? v(b) - v(a) : v(a) - v(b)));
  }, [data, onlyStatus, side, sortKey, sortDesc]);

  const sortBy = (key: SortKey) => {
    if (key === sortKey) setSortDesc(!sortDesc);
    else {
      setSortKey(key);
      setSortDesc(true);
    }
  };
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

  const plan = data?.plan;
  const vsCost = data?.close && data.long_avg_cost ? (data.close / data.long_avg_cost - 1) * 100 : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
      {/* Banner */}
      <div style={{ ...panel, padding: '1.5rem', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
            <Users size={28} style={{ color: '#2dd4bf' }} />
            <h2 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 700 }}>Broker Flow</h2>
          </div>
          <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '0.9rem', maxWidth: 760 }}>
            Each broker's position in one stock since the start date: average cost, floating and realized P/L, and what it did over the
            last few sessions (accumulating, averaging down, trimming, cutting loss). Data comes from your own API key and is cached on
            this machine, so each stock-day is requested only once.
          </p>
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', textAlign: 'right' }}>
          {status?.configured ? (
            <>
              Provider <b style={{ color: '#2dd4bf' }}>{status.provider}</b>
              <br />
              {fmtInt(status.usage.today)} / {fmtInt(status.daily_cap)} requests today · {fmtInt(status.usage.month)} this month
            </>
          ) : (
            <span style={{ color: '#facc15' }}>No API key set: cached data only</span>
          )}
        </div>
      </div>

      {status && !status.configured && (
        <div style={{ ...panel, display: 'flex', gap: '0.75rem', alignItems: 'flex-start', borderColor: 'rgba(250, 204, 21, 0.3)' }}>
          <KeyRound size={20} style={{ color: '#facc15', flexShrink: 0, marginTop: 2 }} />
          <div style={{ fontSize: '0.85rem', color: '#cbd5e1' }}>
            Add your own key to <code>.env</code> and restart the containers (<code>docker compose up -d</code>):{' '}
            <code>INVEZGO_API_KEY=…</code> (Invezgo developer plan) or <code>SECTORS_API_KEY=…</code> (Sectors Insider). Use the data
            within your provider's terms; it stays on this machine.
          </div>
        </div>
      )}

      {/* Controls */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
        style={{ ...panel, display: 'flex', gap: '0.6rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.9rem 1.25rem' }}
      >
        <input
          id="bf-ticker"
          className="form-control"
          style={{ maxWidth: '130px', paddingLeft: '1rem', textTransform: 'uppercase' }}
          placeholder="Ticker…"
          list="bf-cached"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
        />
        <datalist id="bf-cached">
          {(status?.stocks ?? []).map((s) => (
            <option key={s.stock} value={s.stock}>{`${s.days} days cached`}</option>
          ))}
        </datalist>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          From <input id="bf-from" type="date" className="form-control" style={{ display: 'inline-block', width: 'auto' }} value={from} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
          To <input id="bf-to" type="date" className="form-control" style={{ display: 'inline-block', width: 'auto' }} value={to} onChange={(e) => setTo(e.target.value)} />
        </label>
        <select id="bf-recent" className="form-control" style={{ maxWidth: '190px' }} value={recent} onChange={(e) => setRecent(Number(e.target.value))}>
          {[1, 5, 10, 20].map((n) => (
            <option key={n} value={n}>{`Status over last ${n} day${n > 1 ? 's' : ''}`}</option>
          ))}
        </select>
        <button type="submit" style={btn(true)} disabled={!ticker.trim() || loading}>
          {loading ? 'Loading…' : 'Show positions'}
        </button>
      </form>

      {error && (
        <div style={{ ...panel, color: '#f87171', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          <AlertTriangle size={18} /> {error}
        </div>
      )}

      {loading && (
        <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-secondary)' }}>
          <Loader2 size={32} className="spinning" style={{ color: '#38bdf8' }} />
        </div>
      )}

      {!loading && !data && !error && (
        <div style={{ ...panel, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          Enter a ticker to see its brokers' positions.{' '}
          {status?.stocks.length ? `Cached: ${status.stocks.map((s) => s.stock).join(', ')}.` : 'Nothing cached yet.'}
        </div>
      )}

      {/* Cache coverage and fetch */}
      {!loading && data && plan && (
        <div style={{ ...panel, display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.9rem 1.25rem', fontSize: '0.85rem' }}>
          <span>
            <b>{data.stock}</b>: {fmtInt(plan.cached)} of {fmtInt(plan.trading_days)} trading days cached
            {plan.missing > 0 && (
              <>
                {' '}· {fmtInt(plan.missing)} missing → <b>{fmtInt(plan.requests)}</b> request{plan.requests === 1 ? '' : 's'}
              </>
            )}
          </span>
          {plan.missing > 0 && (
            <button onClick={runFetch} style={btn(false)} disabled={fetching}>
              <Download size={13} style={{ marginRight: 4, verticalAlign: '-2px' }} />
              {fetching ? 'Fetching…' : `Fetch missing days (${plan.requests} requests)`}
            </button>
          )}
          {fetchResult && (
            <span style={{ color: fetchResult.stopped ? '#facc15' : '#34d399' }}>
              Fetched {fmtInt(fetchResult.fetched_days)} days ({fmtInt(fetchResult.requests_made ?? 0)} requests)
              {fetchResult.stopped ? `; stopped: ${fetchResult.stopped}` : ''}
            </span>
          )}
        </div>
      )}

      {!loading && data && data.brokers.length === 0 && (
        <div style={{ ...panel, color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
          No cached broker data for {data.stock} in {data.range.from} → {data.range.to}.
          {data.plan ? ' Fetch the missing days above.' : ' Set an API key to fetch it, or run: idx brokerflow fetch ' + data.stock}
        </div>
      )}

      {!loading && data && data.brokers.length > 0 && (
        <>
          {/* Summary */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: '0.75rem' }}>
            {[
              {
                label: `${data.stock} close`,
                value: `Rp${fmtInt(data.close ?? 0)}`,
                sub: data.first_close ? `from Rp${fmtInt(data.first_close)} on ${data.from}` : '',
              },
              { label: 'Net long brokers hold', value: fmtRpShort(data.net_long_value ?? 0), sub: `${data.days} trading days` },
              {
                label: 'Their average cost',
                value: data.long_avg_cost ? `Rp${fmtInt(data.long_avg_cost)}` : '—',
                sub: vsCost == null ? '' : `close is ${vsCost >= 0 ? '+' : ''}${vsCost.toFixed(1)}% vs cost`,
                color: tone(vsCost),
              },
              { label: 'Brokers', value: fmtInt(data.brokers.length), sub: data.foreign ? 'foreign split available' : 'no foreign split' },
            ].map((k) => (
              <div key={k.label} style={{ ...panel, padding: '1rem' }}>
                <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{k.label}</div>
                <div style={{ fontSize: '1.4rem', fontWeight: 800, color: k.color ?? '#f8fafc', fontVariantNumeric: 'tabular-nums' }}>{k.value}</div>
                <div style={{ fontSize: '0.72rem', color: k.color ?? 'var(--text-secondary)' }}>{k.sub}</div>
              </div>
            ))}
          </div>

          {(data.adjustment_events ?? []).length > 0 && (
            <div style={{ ...panel, display: 'flex', gap: '0.5rem', alignItems: 'center', color: '#facc15', fontSize: '0.82rem', padding: '0.75rem 1.25rem' }}>
              <AlertTriangle size={16} />
              Ex-date inside the window ({data.adjustment_events!.map((e) => `${e.date}, factor ${e.factor}`).join('; ')}): share counts and
              prices before it are on a different basis, so averages across it are distorted. Start the window after the ex-date.
            </div>
          )}

          {/* Filters */}
          <div style={{ ...panel, display: 'flex', gap: '0.4rem', alignItems: 'center', flexWrap: 'wrap', padding: '0.9rem 1.25rem' }}>
            {(
              [
                ['all', 'All'],
                ['long', 'Net long'],
                ['short', 'Net short'],
              ] as [Side, string][]
            ).map(([k, label]) => (
              <button key={k} onClick={() => setSide(k)} style={btn(side === k)}>{label}</button>
            ))}
            <span style={{ width: 1, height: 22, background: 'rgba(255,255,255,0.1)', margin: '0 0.4rem' }} />
            {(Object.keys(STATUS) as BrokerStatus[])
              .filter((s) => data.status_counts[s])
              .map((s) => (
                <button
                  key={s}
                  onClick={() => setOnlyStatus(onlyStatus === s ? null : s)}
                  title={STATUS[s].hint}
                  style={{
                    ...btn(onlyStatus === s),
                    color: STATUS[s].color,
                    borderColor: onlyStatus === s ? STATUS[s].color : 'rgba(255,255,255,0.1)',
                  }}
                >
                  {STATUS[s].label} {data.status_counts[s]}
                </button>
              ))}
            <span style={{ marginLeft: 'auto', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{fmtInt(rows.length)} brokers</span>
          </div>

          {/* Table */}
          <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
            <div className="table-container" style={{ marginTop: 0, maxHeight: '720px', overflowY: 'auto' }}>
              <table style={{ fontSize: '0.82rem' }}>
                <thead>
                  <tr>
                    {th('Broker', undefined, 'left')}
                    {th(`Status (${data.recent_days}d)`, undefined, 'left')}
                    {th('Lots', 'position_value', 'right', 'Net lots held since the start date (sorted by value)')}
                    {th('Avg cost')}
                    {th('Vs cost', 'floating_pct')}
                    {th('Floating P/L', 'floating_pl')}
                    {th('Realized P/L', 'realized_pl')}
                    {th('Total P/L', 'total_pl')}
                    {th(`Net lots ${data.recent_days}d`, 'recent_net_shares')}
                    {th('Volume %', 'share_of_volume', 'right', "Broker's share of the stock's traded volume in the window")}
                    {data.foreign && th('Foreign net')}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((b) => (
                    <tr key={b.broker}>
                      <td style={{ fontWeight: 700 }}>{b.broker}</td>
                      <td>
                        <span
                          title={STATUS[b.status].hint}
                          style={{
                            color: STATUS[b.status].color,
                            border: `1px solid ${STATUS[b.status].color}55`,
                            borderRadius: '8px',
                            padding: '0.1rem 0.45rem',
                            fontSize: '0.72rem',
                            fontWeight: 700,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {STATUS[b.status].label}
                        </span>
                      </td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.position) }}>{fmtInt(b.position / 100)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{b.avg_cost ? fmtInt(b.avg_cost) : '—'}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.floating_pct) }}>
                        {b.floating_pct == null ? '—' : `${b.floating_pct >= 0 ? '+' : ''}${b.floating_pct.toFixed(1)}%`}
                      </td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.floating_pl) }}>{fmtRpShort(b.floating_pl)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.realized_pl) }}>{fmtRpShort(b.realized_pl)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.total_pl), fontWeight: 700 }}>{fmtRpShort(b.total_pl)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.recent_net_shares) }}>{fmtInt(b.recent_net_shares / 100)}</td>
                      <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{b.share_of_volume.toFixed(1)}</td>
                      {data.foreign && (
                        <td style={{ textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: tone(b.foreign_net_value) }}>
                          {b.foreign_net_value == null ? '—' : fmtRpShort(b.foreign_net_value)}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-start', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
            <Info size={14} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              Positions start at zero on {data.from}: a broker's holdings from before then are unknown, and a negative position means it sold
              more than it bought in the window. One broker code serves many clients, so a position is not one investor's. Same-day buys and
              sells are booked as round trips; only the net changes the position.{' '}
              <a onClick={() => onSelectStock(data.stock)} style={{ cursor: 'pointer', color: '#38bdf8' }}>Open {data.stock} chart</a>
            </span>
          </div>
        </>
      )}
    </div>
  );
};
