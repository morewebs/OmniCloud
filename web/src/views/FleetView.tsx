import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Divider from '@mui/material/Divider';
import InputAdornment from '@mui/material/InputAdornment';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Skeleton from '@mui/material/Skeleton';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TablePagination from '@mui/material/TablePagination';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import SearchIcon from '@mui/icons-material/Search';
import { api, fmtBytes, fmtMoney, fmtTime, post } from '../api';
import type { AdapterInfo, FleetResponse, Server } from '../types';
import { AllowanceMeter } from '../components/AllowanceMeter';
import { StatusBadge } from '../components/StatusBadge';
import { Value, StaleStamp } from '../components/Value';
import { Sparkline } from '../components/Sparkline';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { FirewallDialog, AttachFirewallDialog, CreateFirewallDialog, toHetznerRules } from '../components/FirewallDialog';
import { PageHeader } from '../components/PageHeader';


export function FleetView() {
  const qc = useQueryClient();
  const fleet = useQuery<FleetResponse>({ queryKey: ['fleet'],
    queryFn: () => api<FleetResponse>('/api/fleet') });
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const [search, setSearch] = useState(() =>
    new URLSearchParams(window.location.search).get('q') ?? '');
  const [detail, setDetail] = useState<Server | null>(null);
  // Large fleets: paginate the table (25/50/100 per page) instead of
  // rendering hundreds of meter rows at once.
  const [page, setPage] = useState(0);
  const [rowsPerPage, setRowsPerPage] = useState(25);

  const rows = useMemo(() => {
    const all: Server[] = fleet.data?.accounts.flatMap(a => a.servers) ?? [];
    const q = search.toLowerCase();
    return all.filter(s => !q || s.name.toLowerCase().includes(q)
      || (s.ipv4 ?? '').includes(q) || s.adapter.includes(q)
      || (s.region ?? '').toLowerCase().includes(q));
  }, [fleet.data, search]);

  const pagedRows = useMemo(
    () => rows.slice(page * rowsPerPage, (page + 1) * rowsPerPage),
    [rows, page, rowsPerPage]);

  const allServers = fleet.data?.accounts.flatMap(a => a.servers) ?? [];
  const summary = useMemo(() => {
    const running = allServers.filter(s => s.status === 'running').length;
    const notReporting = allServers.filter(s => s.status === 'unknown').length;
    const traffic = allServers.reduce((acc, s) => acc + (s.allowance?.used_bytes ?? 0), 0);
    const overage = allServers.reduce(
      (acc, s) => acc + Number(s.allowance?.projected_overage_cost?.amount ?? 0), 0);
    return { total: allServers.length, running, notReporting, traffic, overage };
  }, [allServers]);

  if (fleet.isPending) {
    // Skeletons matching the table rhythm; no spinners anywhere.
    return (
      <Stack spacing={2}>
        {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} variant="rounded" height={44} />)}
      </Stack>
    );
  }
  if (fleet.isError) return <Alert severity="error">{(fleet.error as Error).message}</Alert>;

  if (!fleet.data.accounts.length) {
    return (
      <Stack spacing={2} sx={{ alignItems: "flex-start", py: 8 }}>
        <Typography variant="h6">No servers connected</Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Connect a provider account to import its fleet.
        </Typography>
        <Button variant="contained" href="#/credentials">Connect a provider account</Button>
      </Stack>
    );
  }

  return (
    <Stack spacing={2}>
      <PageHeader
        title="Fleet"
        subtitle={`${summary.total} servers · ${summary.running} running · ${summary.notReporting} unknown · ${fmtBytes(summary.traffic)} this month · projected overage €${summary.overage.toFixed(2)}`}
      />

      {fleet.data.in_progress_actions.length > 0 && (
        <Alert severity="info" icon={false}>
          <Stack spacing={0.5}>
            {fleet.data.in_progress_actions.map(a => (
              <Typography key={a.id} variant="body2">
                {a.kind} in progress on {a.provider_id} (started {fmtTime(a.created_at)})
              </Typography>
            ))}
          </Stack>
        </Alert>
      )}

      <TextField
        size="small" placeholder="Search name, IP, provider, region"
        value={search} onChange={e => setSearch(e.target.value)}
        sx={{ maxWidth: 340 }}
        slotProps={{ input: { startAdornment: (
          <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
        ) } }}
      />

      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small" sx={{ minWidth: 900 }}>
          <TableHead>
            <TableRow>
              <TableCell>Status</TableCell>
              <TableCell>Name</TableCell>
              <TableCell>Provider</TableCell>
              <TableCell>Region</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>IPv4</TableCell>
              <TableCell>Traffic</TableCell>
              <TableCell align="right">Monthly</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {pagedRows.map(s => {
              const syncInfo = fleet.data!.sync[String(s.account_id)];
              return (
                <TableRow key={`${s.account_id}:${s.provider_id}`} hover
                          onClick={() => setDetail(s)} sx={{ cursor: 'pointer' }}>
                  <TableCell><StatusBadge status={s.status} /></TableCell>
                  <TableCell>
                    <StaleStamp lastSeenAt={s.last_seen_at}
                                intervalMinutes={syncInfo?.interval_minutes}>
                      <Typography variant="body2" sx={{ fontWeight: 500 }}>{s.name}</Typography>
                    </StaleStamp>
                  </TableCell>
                  <TableCell><Chip size="small" variant="outlined" label={s.adapter} /></TableCell>
                  <TableCell>{s.region ?? '—'}</TableCell>
                  <TableCell>
                    <Value value={s.server_type} notExposed={s.not_exposed.includes('server_type')} />
                  </TableCell>
                  <TableCell><span className="num">{s.ipv4 ?? '—'}</span></TableCell>
                  <TableCell>
                    {s.allowance ? <AllowanceMeter allowance={s.allowance} /> : '—'}
                  </TableCell>
                  <TableCell align="right">
                    <Value value={s.monthly_price ? fmtMoney(s.monthly_price) : null}
                           notExposed={s.not_exposed.includes('monthly_price')} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </Box>
      <TablePagination
        component="div"
        count={rows.length}
        page={page}
        onPageChange={(_, p) => setPage(p)}
        rowsPerPage={rowsPerPage}
        onRowsPerPageChange={(e) => { setRowsPerPage(parseInt(e.target.value, 10)); setPage(0); }}
        rowsPerPageOptions={[25, 50, 100]}
      />

      {detail && (
        <ServerDialog
          server={detail}
          isAdmin={me.data?.role === 'admin'}
          capabilities={adapters.data?.find(a => a.key === detail.adapter)?.capabilities ?? []}
          onClose={() => setDetail(null)}
          onDone={() => qc.invalidateQueries()}
        />
      )}
    </Stack>
  );
}

function ServerDialog({ server, isAdmin, capabilities, onClose, onDone }: {
  server: Server; isAdmin: boolean; capabilities: string[]; onClose: () => void; onDone: () => void;
}) {
  const [confirm, setConfirm] = useState<string | null>(null); // capability key
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rename, setRename] = useState(server.name);
  const [fwOpen, setFwOpen] = useState(false);        // manage attached
  const [fwAttachOpen, setFwAttachOpen] = useState(false);  // pick existing
  const [fwCreateOpen, setFwCreateOpen] = useState(false); // create new
  const [attachedFws, setAttachedFws] = useState<{ id: number; name: string }[]>([]);
  const has = (c: string) => capabilities.includes(c); // absent = not rendered

  const allFws = useQuery<{ id: number; name: string; rules: number;
    applied_to_count: number; applied_server_ids: number[] }[]>({
    queryKey: ['firewalls', server.account_id],
    queryFn: () => api(`/api/accounts/${server.account_id}/firewalls`),
    enabled: has('firewall'),
  });

  // Attached firewalls: exact ids from the account's firewalls endpoint
  // (each row knows which servers it's applied to - shared resources).
  const loadAttached = async () => {
    try {
      const rows = await api<{ id: number; name: string; applied_server_ids: number[] }[]>(
        `/api/accounts/${server.account_id}/firewalls`);
      const sid = Number(server.provider_id);
      setAttachedFws(rows.filter(r => r.applied_server_ids.includes(sid))
        .map(r => ({ id: r.id, name: r.name })));
    } catch { /* leave previous */ }
  };
  const openFw = async () => { setFwOpen(true); await loadAttached(); };

  const history = useQuery({
    queryKey: ['server', server.account_id, server.provider_id],
    queryFn: () => api<Server>(`/api/fleet/${server.account_id}/${server.provider_id}`),
  });

  const act = async (kind: string, params: Record<string, unknown> = {}) => {
    setBusy(true); setError(null);
    try {
      await post(`/api/servers/${server.account_id}/${server.provider_id}/actions`,
        { kind, params });
      onDone(); onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false); setConfirm(null);
    }
  };

  const al = server.allowance;

  return (
    <>
      <Dialog open onClose={onClose} maxWidth="md" fullWidth>
        <DialogTitle>{server.name}</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap" }}>
              <StatusBadge status={server.status} />
              <Chip size="small" variant="outlined" label={server.adapter} />
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                {server.provider_id}
              </Typography>
              {server.ipv4 && <Chip size="small" variant="outlined" className="num" label={server.ipv4} />}
              {server.created && (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  created {new Date(server.created).toLocaleDateString()}
                </Typography>
              )}
            </Stack>

            {al && (
              <Box>
                <Stack direction="row" spacing={2} sx={{ alignItems: "center" }}>
                  <AllowanceMeter allowance={al} />
                </Stack>
                <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 0.5 }}>
                  {al.window ?? ''}
                </Typography>
              </Box>
            )}

            <Divider />
            <Stack direction="row" spacing={4}>
              <Stack spacing={0.5}>
                <Typography variant="overline" sx={{ lineHeight: 1.6 }}>Traffic history</Typography>
                <Sparkline values={(history.data?.traffic_history ?? []).map(h => h.bytes_used)} />
              </Stack>
              <Divider orientation="vertical" flexItem />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="overline" sx={{ lineHeight: 1.6 }}>Details</Typography>
                <List dense disablePadding>
                  {server.facets.map(f => (
                    <ListItem key={f.label} sx={{ py: 0 }} secondaryAction={null}>
                      <ListItemText primary={f.value} secondary={f.label}
                                    slotProps={{ primary: { sx: { fontSize: 13 } } }} />
                    </ListItem>
                  ))}
                </List>
              </Box>
            </Stack>

            {isAdmin && (
              <>
                <Divider />
                <Stack direction="row" spacing={1} sx={{ flexWrap: "wrap" }}>
                  {has('rename') && (
                    <TextField size="small" label="Rename" value={rename}
                               onChange={e => setRename(e.target.value)}
                               sx={{ width: 200 }} />
                  )}
                  {has('rename') && (
                    <Button size="small" variant="outlined" disabled={busy || rename === server.name}
                            onClick={() => act('rename', { name: rename })}>Rename</Button>
                  )}
                  {has('reboot') && (
                    <Button size="small" variant="outlined" disabled={busy}
                            onClick={() => act('reboot')}>Reboot</Button>
                  )}
                  {has('shutdown') && (
                    <Button size="small" variant="outlined" disabled={busy}
                            onClick={() => act('shutdown')}>Shut down</Button>
                  )}
                  {has('power_on') && (
                    <Button size="small" variant="outlined" disabled={busy}
                            onClick={() => act('power_on')}>Power on</Button>
                  )}
                  {has('firewall') && (
                    <Button size="small" variant="outlined" disabled={busy}
                            onClick={openFw}>Firewall…</Button>
                  )}
                  {has('firewall') && (
                    <Button size="small" variant="outlined" disabled={busy || allFws.isFetching}
                            onClick={() => { allFws.refetch(); setFwAttachOpen(true); }}>
                      Attach firewall…
                    </Button>
                  )}
                  {has('rebuild') && (
                    <Button size="small" color="warning" disabled={busy}
                            onClick={() => setConfirm('rebuild')}>Rebuild…</Button>
                  )}
                  {has('delete') && (
                    <Button size="small" color="error" disabled={busy}
                            onClick={() => setConfirm('delete')}>Delete…</Button>
                  )}
                </Stack>
              </>
            )}
          </Stack>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirm === 'rebuild'}
        title="Rebuild server"
        serverName={server.name}
        body={`Rebuild ${server.name} from a fresh image. All data on the server is lost. This is irreversible.`}
        confirming={busy}
        error={error}
        onConfirm={() => act('rebuild', { image: 'placeholder-image-id' })}
        onClose={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'delete'}
        title="Delete server"
        serverName={server.name}
        body={`Delete ${server.name} (${server.ipv4 ?? 'no IP'}) permanently. The server and its data are destroyed. This is irreversible.`}
        requireTyped
        confirming={busy}
        error={error}
        onConfirm={() => act('delete')}
        onClose={() => setConfirm(null)}
      />
      <FirewallDialog
        open={fwOpen}
        serverName={server.name}
        attachedFirewalls={attachedFws}
        loading={false}
        error={error}
        onDetach={async (fwId) => {
          setBusy(true); setError(null);
          try {
            await post(`/api/accounts/${server.account_id}/firewalls/${fwId}/detach/${server.provider_id}`);
            await loadAttached();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        onShowCreate={() => { setFwOpen(false); setFwCreateOpen(true); }}
        onClose={() => setFwOpen(false)}
      />
      <AttachFirewallDialog
        open={fwAttachOpen}
        serverName={server.name}
        firewalls={allFws.data ?? []}
        excludeIds={attachedFws.map(f => f.id)}
        loading={allFws.isFetching}
        error={error}
        onAttach={async (fwId) => {
          setBusy(true); setError(null);
          try {
            await post(`/api/accounts/${server.account_id}/firewalls/${fwId}/attach/${server.provider_id}`);
            setFwAttachOpen(false);
            await loadAttached();
            setFwOpen(true);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        onClose={() => setFwAttachOpen(false)}
      />
      <CreateFirewallDialog
        open={fwCreateOpen}
        serverName={server.name}
        busy={busy}
        error={error}
        onCreate={async (rules) => {
          setBusy(true); setError(null);
          try {
            await post(`/api/servers/${server.account_id}/${server.provider_id}/firewall`,
              { rules: toHetznerRules(rules), attach: true });
            setFwCreateOpen(false);
            onDone();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
        onClose={() => setFwCreateOpen(false)}
      />
      <LinearProgress sx={{ display: busy ? 'block' : 'none', position: 'fixed', top: 0, left: 0, right: 0 }} />
    </>
  );
}
