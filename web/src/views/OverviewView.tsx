import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import Button from '@mui/material/Button';
import type { AccountRow, ActionRow } from '../types';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import RadioButtonUncheckedIcon from '@mui/icons-material/RadioButtonUnchecked';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { PieChart } from '@mui/x-charts/PieChart';
import { BarChart } from '@mui/x-charts/BarChart';
import { LineChart } from '@mui/x-charts/LineChart';
import { useTheme } from '@mui/material/styles';
import { api, fmtBytes, fmtCurrency } from '../api';
import { PageHeader } from '../components/PageHeader';
import { usePageTitle } from '../usePageTitle';
import { StatTile } from '../components/StatTile';
import { axisSlotProps, SPEND_COLORS, statusColor } from '../components/charts';

interface OverviewPayload {
  fleet: { total: number; by_status: Record<string, number> };
  spend: Record<string, Record<string, number>>; // adapter -> currency -> amount
  projected_overage: Record<string, number>;
  traffic_days: { day: string; bytes: number }[];
  spend_days: { day: string; spend: Record<string, number> }[];
  recent_actions: { id: number; kind: string; status: string; detail: string | null;
                    created_at: string; username: string | null }[];
  recent_orders: { id: number; status: string; adapter: string; plan_name: string;
                   estimated_monthly: string; created_at: string }[];
  alerts: { kind: string; server?: string; adapter?: string; status?: string;
            pct?: number; account_id?: number; error?: string }[];
}

export function OverviewView() {
  usePageTitle('Overview');
  const mode = useTheme().palette.mode;
  const navigate = useNavigate();
  const ov = useQuery<OverviewPayload>({ queryKey: ['overview'],
    queryFn: () => api<OverviewPayload>('/api/overview') });
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  // "Show all" toggle for the Recent actions card: fetch the fuller log
  // only when asked for (depth on demand, not on first paint).
  const [allActions, setAllActions] = useState(false);
  const actions = useQuery<ActionRow[]>({ queryKey: ['actions'],
    queryFn: () => api<ActionRow[]>('/api/actions'), enabled: allActions });
  // First-run onboarding: an install with no provider accounts yet shows the
  // setup checklist (dismissed per-browser until the first account exists).
  // Must not claim "no accounts" while still fetching or on a failed fetch.
  const onboarding = !accounts.isPending && !accounts.isError
    && (accounts.data?.length ?? 0) === 0
    && localStorage.getItem('omnicloud-onboarded') !== '1';

  if (ov.isPending) return <Typography sx={{ color: 'text.secondary' }}>Loading…</Typography>;
  if (ov.isError) return <Alert severity="error">{(ov.error as Error).message}</Alert>;
  const d = ov.data!;

  const trafficNow = d.traffic_days.at(-1)?.bytes ?? 0;
  const trafficPrev = d.traffic_days.at(-2)?.bytes ?? null;
  // 0 yesterday -> any traffic today is NEW, not "no delta" (the most notable
  // jump was hidden by a falsy-zero check); no traffic today -> no delta
  const delta = trafficNow > 0 && trafficPrev != null
    ? trafficPrev > 0
      ? { text: `${trafficNow >= trafficPrev ? '+' : ''}${(((trafficNow - trafficPrev) / trafficPrev) * 100).toFixed(0)}% vs yesterday`, up: trafficNow >= trafficPrev }
      : { text: 'new traffic — none yesterday', up: true }
    : null;

  const statusData = Object.entries(d.fleet.by_status).map(([label, value]) => ({
    id: label, value, label, color: statusColor(label, mode),
  }));

  const spendRows = Object.entries(d.spend).flatMap(([adapter, byCur]) =>
    Object.entries(byCur).map(([currency, amount]) => ({ adapter, currency, amount })));
  const adapters = [...new Set(spendRows.map(r => r.adapter))];
  const currencies = [...new Set(spendRows.map(r => r.currency))];
  // one series per currency - mixed currencies are never summed
  const spendSeries = currencies.map((cur, i) => ({
    label: cur,
    color: SPEND_COLORS[mode][i % SPEND_COLORS[mode].length],
    data: adapters.map(a =>
      spendRows.find(r => r.adapter === a && r.currency === cur)?.amount ?? 0),
  }));

  const overageEntries = Object.entries(d.projected_overage);
  const downAlerts = d.alerts.filter(a => a.kind === 'down');
  // owner's first question: what will I pay? base + projected overage, per
  // currency, joined (never summed across currencies)
  // merge spend per currency across adapters FIRST, then add that
  // currency's overage once (per-adapter rows double-counted the overage
  // and showed "€X + €Y" for one currency)
  const baseByCur = new Map<string, number>();
  for (const byCur of Object.values(d.spend))
    for (const [cur, amt] of Object.entries(byCur))
      baseByCur.set(cur, (baseByCur.get(cur) ?? 0) + amt);
  const bill = [...new Set([...baseByCur.keys(), ...Object.keys(d.projected_overage)])]
    .map(cur => [cur, (baseByCur.get(cur) ?? 0) + (d.projected_overage[cur] ?? 0)] as const);
  const accountName = (id: number) =>
    accounts.data?.find(a => a.id === id)?.name ?? `account ${id}`;
  // non-alert pick for the summary Alert: down > allowance > sync
  const worst = downAlerts[0]
    ?? d.alerts.find(a => (a.pct ?? 0) >= 100)
    ?? d.alerts.find(a => a.kind === 'billing' && /OVERDUE/.test(a.error ?? ''))
    ?? d.alerts.find(a => a.kind !== 'down')
    ?? d.alerts[0];

  return (
    <Stack spacing={3}>
      <PageHeader title="Overview" subtitle="Fleet condition, spend, and traffic — every number provider-reported." />

      {onboarding && (
        <Card>
          <CardContent>
            <Stack spacing={1.5}>
              <Typography variant="h5">Set up your panel</Typography>
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                Three steps and your fleet is on the panel.
              </Typography>
              {[
                ['Add a provider account', 'Credentials holds your Hetzner or LeaseWeb API token - encrypted, never displayed back.',
                 '/credentials', 'Open Credentials'],
                ['Wait for the first sync', 'The panel imports the fleet and its traffic numbers on its own (a minute or two).',
                 null, null],
                ['Compare plans, order prototypes', 'The catalog carries all 7 providers with prices, traffic rules, and extra-IP costs.',
                 '/catalog', 'Open Catalog'],
              ].map(([title, line, to, cta], i) => (
                <Stack key={i} direction="row" spacing={1.5} sx={{ alignItems: 'flex-start' }}>
                  {i === 0 ? <CheckCircleIcon sx={{ color: 'success.main', mt: 0.5 }} />
                           : <RadioButtonUncheckedIcon sx={{ color: 'text.secondary', mt: 0.5 }} />}
                  <Stack sx={{ flex: 1 }}>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{title}</Typography>
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>{line}</Typography>
                  </Stack>
                  {to && cta && <Button size="small" onClick={() => navigate(to as string)}>{cta}</Button>}
                </Stack>
              ))}
              <Button size="small" sx={{ alignSelf: 'flex-start' }}
                      onClick={() => { localStorage.setItem('omnicloud-onboarded', '1'); location.reload(); }}>
                Dismiss
              </Button>
            </Stack>
          </CardContent>
        </Card>
      )}

      {/* the owner's two questions, first screen-inch: is anything down,
          and what will I pay. Down outranks everything. */}
      {downAlerts.length > 0 && (
        <Alert severity="error" icon={false} sx={{ alignItems: 'center' }}>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
            <Box>
              <b>{downAlerts.length} server{downAlerts.length > 1 ? 's' : ''} need attention</b>
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                {downAlerts.slice(0, 3).map(a =>
                  `${a.server} ${a.status === 'off' ? 'is off' : 'stopped reporting'}`).join(', ')}
                {downAlerts.length > 3 ? ` and ${downAlerts.length - 3} more` : ''}.
                {' '}
                <Button size="small" onClick={() => navigate('/fleet')}>Open Fleet</Button>
              </Typography>
            </Box>
          </Stack>
        </Alert>
      )}

      {/* The instrument row: traffic dominates (design.md 5), fleet is a
          sentence, overage carries the one decision the operator may owe. */}
      <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} useFlexGap sx={{ gap: 2 }}>
        <StatTile label="Traffic today" value={fmtBytes(trafficNow)}
                  delta={delta} sub="out, summed across synced servers"
                  dominant /* rising traffic = overage risk, not good: no deltaGood */ />
        <Stack spacing={2} sx={{ flex: 1, minWidth: 0 }}>
          <StatTile label="This month's bill"
                    value={bill.length
                      ? bill.map(([cur, total]) => fmtCurrency(total, cur)).join(' + ')
                      : '—'}
                    sub={bill.length ? "what you'll pay about, if nothing changes" : 'no priced servers yet'} />
          <StatTile label="Fleet" value={String(d.fleet.total)} unit="servers"
                    sub={fleetSentence(d.fleet.by_status)} />
          <StatTile label="Projected overage"
                    value={overageEntries.length
                      ? overageEntries.map(([c, a]) => fmtCurrency(a, c)).join(' + ')
                      : '—'}
                    sub={overageEntries.length ? 'this month, adapter-reported' : 'none projected'} />
        </Stack>
      </Stack>

      {/* alerts lead the second row: the loudest thing on screen is the one
          thing that needs a decision (down-alerts already got their banner
          above; this one is for allowance + sync) */}
      {d.alerts.length > 0 && worst && worst.kind !== 'down' && (
        <Alert severity={(worst.pct ?? 0) >= 100 || /OVERDUE/.test(worst.error ?? '')
                         ? 'error' : 'warning'} icon={false}
               sx={{ alignItems: 'center' }}>
          <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap', gap: 1 }}>
            <Box>
              {worst.kind === 'allowance'
                ? <b>{worst.server}</b>
                : <b>{accountName(worst.account_id ?? 0)}</b>}
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                {worst.kind === 'allowance'
                  ? `at ${worst.pct}% of its traffic allowance`
                  : worst.error}
              </Typography>
            </Box>
            {d.alerts.length - downAlerts.length > 1 && (
              <Tooltip title={d.alerts.filter(a => a.kind !== 'down').map(a => a.kind === 'allowance'
                ? `${a.server} (${a.adapter}) — ${a.pct}%`
                : `${accountName(a.account_id ?? 0)}: ${a.error}`).join('\n')}>
                <Chip size="small" variant="outlined"
                      label={`+${d.alerts.length - downAlerts.length - 1} more`} />
              </Tooltip>
            )}
          </Stack>
        </Alert>
      )}

      <Stack direction={{ xs: 'column', lg: 'row' }} spacing={2} useFlexGap sx={{ gap: 2 }}>
        <Card sx={{ flex: 1, minWidth: { xs: 0, lg: 260 } }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Fleet status</Typography>
            {statusData.length
              ? <PieChart
                  height={190}
                  series={[{
                    data: statusData,
                    innerRadius: 34, outerRadius: 72, paddingAngle: 2, cornerRadius: 4,
                  }]}
                  hideLegend
                />
              : <Empty text="No servers yet" />}
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 0.5, mt: 1 }}>
              {statusData.map(s => (
                <Chip key={s.label} size="small" variant="outlined"
                      label={`${s.value} ${s.label}`} />
              ))}
            </Stack>
          </CardContent>
        </Card>

        <Card sx={{ flex: 1.4, minWidth: { xs: 0, lg: 300 }, maxWidth: '100%' }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Monthly spend by provider</Typography>
            {spendRows.length
              ? <BarChart
                  height={190}
                  xAxis={[{ data: adapters, scaleType: 'band' }]}
                  series={currencies.length > 1
                    // mixed currencies: side-by-side bars, NEVER stacked
                    // (a stack visually sums EUR + USD)
                    ? spendSeries
                    : spendSeries.map(s => ({ ...s, stack: 'total' }))}
                  slotProps={axisSlotProps}
                  hideLegend={currencies.length < 2}
                />
              : <Empty text="No spend data yet" />}
            {!spendRows.length && (
              <Typography variant="caption" sx={{ color: 'text.secondary', fontStyle: 'italic' }}>
                some providers do not expose per-server prices
              </Typography>
            )}
          </CardContent>
        </Card>

        <Card sx={{ flex: 1.6, minWidth: { xs: 0, lg: 300 }, maxWidth: '100%' }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Spend over time</Typography>
            {/* union currencies across ALL days - a currency first appearing
                mid-window still gets its series (the oldest day's keys alone
                would silently drop it) */}
            {(() => {
              const spendCurrencies = [...new Set(
                d.spend_days.flatMap(x => Object.keys(x.spend)))];
              return d.spend_days.length > 1 && spendCurrencies.length
                ? <LineChart
                    height={190}
                    xAxis={[{
                      data: d.spend_days.map(x => x.day.slice(5)),
                      scaleType: 'point',
                    }]}
                    series={spendCurrencies.map((cur, i) => ({
                      data: d.spend_days.map(x => x.spend[cur] ?? 0),
                      color: SPEND_COLORS[mode][i % SPEND_COLORS[mode].length],
                      label: `${cur}/mo`, showMark: false, curve: 'linear',
                    }))}
                    slotProps={axisSlotProps}
                    hideLegend={spendCurrencies.length < 2}
                  />
                : <Empty text="Spend history builds up as servers are added" />;
            })()}
          </CardContent>
        </Card>

        <Card sx={{ flex: 1.6, minWidth: { xs: 0, lg: 300 }, maxWidth: '100%' }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Traffic, last 30 days</Typography>
            {d.traffic_days.length > 1
              ? <LineChart
                  height={190}
                  xAxis={[{
                    data: d.traffic_days.map(x => x.day.slice(5)),
                    scaleType: 'point',
                  }]}
                  series={[{
                    data: d.traffic_days.map(x => x.bytes / 1e9),
                    color: SPEND_COLORS[mode][0], label: 'GB/day', showMark: false, curve: 'linear',
                  }]}
                  yAxis={[{ label: 'GB' }]}
                  slotProps={axisSlotProps}
                  hideLegend
                />
              : <Empty text="Traffic history builds up over the first days" />}
          </CardContent>
        </Card>
      </Stack>

      <Stack direction={{ xs: 'column', lg: 'row' }} spacing={2} useFlexGap sx={{ gap: 2 }}>
        <Card sx={{ flex: 1, minWidth: { xs: 0, lg: 280 }, maxWidth: '100%' }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Recent actions</Typography>
            <Stack spacing={1.5} sx={{ mt: 1 }}>
              {(allActions ? (actions.data ?? []) : d.recent_actions).map(a => (
                <Stack key={a.id} direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {a.kind} <Box component="span" sx={{ color: 'text.secondary'  }}>· {a.username ?? 'system'}</Box>
                  </Typography>
                  <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline' }}>
                    <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                      {new Date(a.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </Typography>
                    <Chip size="small" variant="outlined"
                          color={a.status === 'done' ? 'success' : a.status === 'failed' ? 'error' : undefined}
                          label={a.status} />
                  </Stack>
                </Stack>
              ))}
              {allActions && (actions.data?.length ?? 0) === 0 && <Empty text="No actions yet" />}
              {!allActions && d.recent_actions.length === 0 && <Empty text="No actions yet" />}
              <Button size="small" sx={{ alignSelf: 'flex-start' }}
                      onClick={() => setAllActions(o => !o)}>
                {allActions ? 'Show recent only' : 'Show all (up to 100)'}
              </Button>
            </Stack>
          </CardContent>
        </Card>

        <Card sx={{ flex: 1, minWidth: { xs: 0, lg: 280 }, maxWidth: '100%' }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Recent orders</Typography>
            <Stack spacing={1.5} sx={{ mt: 1 }}>
              {d.recent_orders.length === 0 && <Empty text="No orders yet" />}
              {d.recent_orders.map(o => (
                <Stack key={o.id} direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {o.plan_name} <Box component="span" sx={{ color: 'text.secondary'  }}>· {o.adapter}</Box>
                  </Typography>
                  <Chip size="small" variant="outlined" label={o.status}
                        color={o.status === 'provisioned' ? 'success'
                          : o.status === 'failed' ? 'error' : undefined} />
                </Stack>
              ))}
            </Stack>
          </CardContent>
        </Card>
      </Stack>
    </Stack>
  );
}

/** "188 of 200 reporting · 11 unknown · 1 off" — count-first sentence. */
function fleetSentence(byStatus: Record<string, number>): string {
  const total = Object.values(byStatus).reduce((a, b) => a + b, 0);
  const running = byStatus['running'] ?? 0;
  const parts = [`${running} of ${total} reporting`];
  for (const [k, v] of Object.entries(byStatus)) {
    if (k !== 'running' && v > 0) parts.push(`${v} ${k}`);
  }
  return parts.join(' · ');
}

function Empty({ text }: { text: string }) {
  return (
    <Box sx={{ height: 190, display: 'grid', placeItems: 'center' }}>
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>{text}</Typography>
    </Box>
  );
}
