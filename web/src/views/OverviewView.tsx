import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import { PieChart } from '@mui/x-charts/PieChart';
import { BarChart } from '@mui/x-charts/BarChart';
import { LineChart } from '@mui/x-charts/LineChart';
import { api, fmtBytes } from '../api';
import { PageHeader } from '../components/PageHeader';
import { StatTile } from '../components/StatTile';
import { Sparkline } from '../components/Sparkline';

interface OverviewPayload {
  fleet: { total: number; by_status: Record<string, number> };
  spend: Record<string, Record<string, number>>; // adapter -> currency -> amount
  projected_overage: Record<string, number>;
  traffic_days: { day: string; bytes: number }[];
  recent_actions: { id: number; kind: string; status: string; detail: string | null;
                    created_at: string; username: string | null }[];
  recent_orders: { id: number; status: string; adapter: string; plan_name: string;
                   estimated_monthly: string; created_at: string }[];
  alerts: { kind: string; server?: string; adapter?: string; pct?: number;
            account_id?: number; error?: string }[];
}

function fmtCurrency(amount: number, currency: string) {
  return new Intl.NumberFormat('en', { style: 'currency', currency, maximumFractionDigits: 0 }).format(amount);
}

export function OverviewView() {
  const ov = useQuery<OverviewPayload>({ queryKey: ['overview'],
    queryFn: () => api<OverviewPayload>('/api/overview') });

  if (ov.isPending) return <Typography sx={{ color: 'text.secondary' }}>Loading…</Typography>;
  if (ov.isError) return <Alert severity="error">{(ov.error as Error).message}</Alert>;
  const d = ov.data!;

  const trafficNow = d.traffic_days.at(-1)?.bytes ?? 0;
  const trafficPrev = d.traffic_days.at(-2)?.bytes ?? null;
  const delta = trafficPrev && trafficPrev > 0
    ? { text: `${trafficNow >= trafficPrev ? '+' : ''}${(((trafficNow - trafficPrev) / trafficPrev) * 100).toFixed(0)}% vs yesterday`, up: trafficNow >= trafficPrev }
    : null;

  const statusData = Object.entries(d.fleet.by_status).map(([label, value], i) => ({
    id: i, value, label,
  }));

  const spendRows = Object.entries(d.spend).flatMap(([adapter, byCur]) =>
    Object.entries(byCur).map(([currency, amount]) => ({ adapter, currency, amount })));
  const adapters = [...new Set(spendRows.map(r => r.adapter))];
  const currencies = [...new Set(spendRows.map(r => r.currency))];
  // one series per currency - mixed currencies are never summed
  const spendSeries = currencies.map(cur => ({
    label: cur,
    data: adapters.map(a =>
      spendRows.find(r => r.adapter === a && r.currency === cur)?.amount ?? 0),
  }));

  const overageEntries = Object.entries(d.projected_overage);

  return (
    <Stack spacing={3}>
      <PageHeader title="Overview" subtitle="Fleet health, spend, and traffic at a glance." />

      <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', gap: 2 }}>
        <StatTile label="Fleet" value={String(d.fleet.total)} unit="servers"
                  sub={`${d.fleet.by_status['running'] ?? 0} running · ${d.fleet.by_status['unknown'] ?? 0} unknown`} />
        <StatTile label="Traffic today" value={fmtBytes(trafficNow)} delta={delta}
                  sub="sum of synced servers" dominant />
        <StatTile label="Projected overage"
                  value={overageEntries.length
                    ? overageEntries.map(([c, a]) => fmtCurrency(a, c)).join(' + ')
                    : '—'}
                  sub={overageEntries.length ? 'this month, adapter-reported' : 'none projected'} />
        <StatTile label="Open orders"
                  value={String(d.recent_orders.length)}
                  sub="last 5 shown below" />
      </Stack>

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} useFlexGap sx={{ gap: 2 }}>
        <Card sx={{ flex: 1, minWidth: 260 }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Fleet status</Typography>
            {statusData.length
              ? <PieChart
                  height={180}
                  series={[{
                    data: statusData,
                    innerRadius: 30, outerRadius: 70, paddingAngle: 2, cornerRadius: 4,
                  }]}
                  hideLegend
                />
              : <Empty text="No servers yet" />}
            <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 0.5, mt: 1 }}>
              {statusData.map(s => (
                <Chip key={s.label} size="small" variant="outlined"
                      label={`${s.label} ${s.value}`} />
              ))}
            </Stack>
          </CardContent>
        </Card>

        <Card sx={{ flex: 1.4, minWidth: 300 }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Monthly spend by provider</Typography>
            {spendRows.length
              ? <BarChart
                  height={180}
                  xAxis={[{ data: adapters, scaleType: 'band' }]}
                  series={spendSeries.map(s => ({ ...s, stack: 'total' }))}
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

        <Card sx={{ flex: 1.6, minWidth: 300 }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Traffic, last 30 days</Typography>
            {d.traffic_days.length > 1
              ? <LineChart
                  height={180}
                  xAxis={[{
                    data: d.traffic_days.map(x => x.day.slice(5)),
                    scaleType: 'point',
                  }]}
                  series={[{
                    data: d.traffic_days.map(x => x.bytes / 1e9),
                    label: 'GB/day', showMark: false, curve: 'linear',
                  }]}
                  yAxis={[{ label: 'GB' }]}
                  hideLegend
                />
              : <Empty text="Traffic history builds up over the first days" />}
          </CardContent>
        </Card>
      </Stack>

      {d.alerts.length > 0 && (
        <Card>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Alerts</Typography>
            <Stack spacing={1} sx={{ mt: 1 }}>
              {d.alerts.slice(0, 8).map((a, i) => (
                <Stack key={i} direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                  <Chip size="small" variant="outlined"
                        color={a.kind === 'allowance' ? 'warning' : 'error'}
                        label={a.kind === 'allowance'
                          ? `${a.pct}% of allowance` : 'sync error'} />
                  <Typography variant="body2">
                    {a.kind === 'allowance'
                      ? `${a.server} (${a.adapter})`
                      : `account ${a.account_id}: ${a.error}`}
                  </Typography>
                </Stack>
              ))}
            </Stack>
          </CardContent>
        </Card>
      )}

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} useFlexGap sx={{ gap: 2 }}>
        <Card sx={{ flex: 1, minWidth: 280 }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Recent actions</Typography>
            <Stack spacing={1.5} sx={{ mt: 1 }}>
              {d.recent_actions.length === 0 && <Empty text="No actions yet" />}
              {d.recent_actions.map(a => (
                <Stack key={a.id} direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {a.kind} <span style={{ color: 'text.secondary' }}>· {a.username ?? 'system'}</span>
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
            </Stack>
          </CardContent>
        </Card>

        <Card sx={{ flex: 1, minWidth: 280 }}>
          <CardContent>
            <Typography variant="overline" sx={{ color: 'text.secondary' }}>Recent orders</Typography>
            <Stack spacing={1.5} sx={{ mt: 1 }}>
              {d.recent_orders.length === 0 && <Empty text="No orders yet" />}
              {d.recent_orders.map(o => (
                <Stack key={o.id} direction="row" sx={{ justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {o.plan_name} <span style={{ color: 'text.secondary' }}>· {o.adapter}</span>
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

function Empty({ text }: { text: string }) {
  return (
    <Box sx={{ height: 180, display: 'grid', placeItems: 'center' }}>
      <Stack spacing={1} sx={{ alignItems: 'center' }}>
        <Sparkline values={[]} />
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>{text}</Typography>
      </Stack>
    </Box>
  );
}
