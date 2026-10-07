import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import IconButton from '@mui/material/IconButton';
import InputAdornment from '@mui/material/InputAdornment';
import MenuItem from '@mui/material/MenuItem';
import Skeleton from '@mui/material/Skeleton';
import Switch from '@mui/material/Switch';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import VisibilityIcon from '@mui/icons-material/Visibility';
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff';
import { api, del, fmtRelative, patch, post } from '../api';
import type { AccountRow, AdapterInfo, CredentialField } from '../types';
import { PageHeader } from '../components/PageHeader';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { Toast } from '../components/Toast';
import type { ToastMsg } from '../components/Toast';
import { usePageTitle } from '../usePageTitle';

// Credential rows show metadata only - the secret is never retrievable.
export function CredentialsView() {
  usePageTitle('Credentials');
  const qc = useQueryClient();
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const me = useQuery({ queryKey: ['me'],
    queryFn: () => api<{ role: string }>('/api/auth/me') });
  const isAdmin = me.data?.role === 'admin';
  const [addOpen, setAddOpen] = useState(false);
  const [toast, setToast] = useState<ToastMsg>(null);
  const [removeAcct, setRemoveAcct] = useState<AccountRow | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [purchasesAcct, setPurchasesAcct] = useState<AccountRow | null>(null);

  if (accounts.isPending) {
    return <Stack spacing={1.5}>
      <Skeleton variant="rounded" height={32} sx={{ maxWidth: 220 }} />
      {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} variant="rounded" height={44} />)}
    </Stack>;
  }
  if (accounts.isError) return (
    <Stack spacing={2}>
      <PageHeader title="Credentials" />
      <Alert severity="error"
             action={<Button onClick={() => accounts.refetch()}>Retry</Button>}>
        {(accounts.error as Error).message}
      </Alert>
    </Stack>
  );

  const doSync = async (a: AccountRow) => {
    setBusyId(a.id);
    try {
      await post(`/api/accounts/${a.id}/sync`);
      setToast({ message: `Syncing ${a.name} — results land in the next refresh`, severity: 'info' });
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['fleet'] });
    } catch (e) {
      setToast({ message: `${a.name}: ${(e as Error).message}`, severity: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  // turning purchases ON goes through a typed confirm (it lets the panel
  // spend money at this provider); turning them OFF is immediate
  const setPurchases = async (a: AccountRow, on: boolean) => {
    setBusyId(a.id);
    try {
      await patch(`/api/accounts/${a.id}`, { purchases_enabled: on });
      qc.invalidateQueries({ queryKey: ['accounts'] });
      setToast({ message: on ? `${a.name}: purchases enabled - IP changes and orders now spend money`
                             : `${a.name}: purchases disabled`, severity: on ? 'info' : 'success' });
      setPurchasesAcct(null);
    } catch (e) {
      setToast({ message: `${a.name}: ${(e as Error).message}`, severity: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  const doRemove = async (a: AccountRow) => {
    setBusyId(a.id);
    try {
      await del(`/api/accounts/${a.id}`);
      qc.invalidateQueries({ queryKey: ['accounts'] });
      qc.invalidateQueries({ queryKey: ['fleet'] });
      setToast({ message: `${a.name} removed — its cached data is gone; the provider account is untouched`, severity: 'success' });
    } catch (e) {
      setToast({ message: `${a.name}: ${(e as Error).message}`, severity: 'error' });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Stack spacing={3}>
      <PageHeader title="Credentials"
        subtitle="Provider API tokens, stored encrypted — only the last 4 characters ever come back out."
        actions={isAdmin && (
          <Button variant="contained" size="small" onClick={() => setAddOpen(true)}>
            Add account
          </Button>)} />

      {accounts.data!.length === 0 && (
        <Alert severity="info" icon={false}>
          No provider accounts yet. Add one to import its fleet — Hetzner,
          LeaseWeb and OVHcloud adapters are ready; the other four providers
          feed the plan catalog without credentials.
        </Alert>
      )}

      {accounts.data!.length > 0 && (
        <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Provider</TableCell>
              <TableCell>Account</TableCell>
              <TableCell>Token</TableCell>
              <TableCell>Added</TableCell>
              <TableCell>Last sync</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>
                <Tooltip title="Real purchases through this account's provider API: extra IPs, IP changes, server orders. Off by default.">
                  <span>Purchases</span>
                </Tooltip>
              </TableCell>
              {isAdmin && <TableCell align="right">Actions</TableCell>}
            </TableRow>
          </TableHead>
          <TableBody>
            {accounts.data!.map(a => (
              <TableRow key={a.id}>
                <TableCell><Chip size="small" variant="outlined" label={a.adapter} /></TableCell>
                <TableCell>{a.name}</TableCell>
                <TableCell><span className="num">••••{a.last4}</span></TableCell>
                <TableCell className="num">{a.cred_created?.slice(0, 10) ?? '—'}</TableCell>
                <TableCell>
                  <Tooltip title={a.last_success_at ?? 'never synced'}>
                    <span className="num">
                      {a.last_success_at ? fmtRelative(a.last_success_at) : '—'}
                    </span>
                  </Tooltip>
                </TableCell>
                <TableCell>
                  {a.last_error
                    ? <Tooltip title={a.last_error}>
                        <Chip size="small" color="error" variant="outlined" label="sync error" />
                      </Tooltip>
                    : a.enabled
                      ? <Chip size="small" color="success" variant="outlined" label="enabled" />
                      : <Chip size="small" variant="outlined" label="disabled" />}
                </TableCell>
                <TableCell>
                  {isAdmin
                    ? <Switch size="small" checked={!!a.purchases_enabled}
                              disabled={busyId === a.id}
                              slotProps={{ input: { 'aria-label': `Purchases for ${a.name}` } }}
                              onChange={e => e.target.checked
                                ? setPurchasesAcct(a) : void setPurchases(a, false)} />
                    : <span>{a.purchases_enabled ? 'on' : 'off'}</span>}
                </TableCell>
                {isAdmin && (
                  <TableCell align="right">
                    <Stack direction="row" spacing={1} sx={{ justifyContent: "flex-end" }}>
                      <Button size="small" disabled={busyId === a.id}
                              onClick={() => doSync(a)}>
                        {busyId === a.id ? 'Syncing…' : 'Sync'}
                      </Button>
                      <Button size="small" color="error" disabled={busyId === a.id}
                              onClick={() => setRemoveAcct(a)}>
                        Remove
                      </Button>
                    </Stack>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Box>
      )}

      <ConfirmDialog
        open={!!removeAcct}
        title="Remove account"
        serverName={removeAcct?.name ?? ''}
        body={`Remove ${removeAcct?.name} from the panel. Its cached servers and history are deleted; the provider account itself is untouched.`}
        confirmLabel="Remove"
        confirming={busyId === removeAcct?.id}
        onConfirm={() => { if (removeAcct) void doRemove(removeAcct); }}
        onClose={() => setRemoveAcct(null)}
      />

      <ConfirmDialog
        open={!!purchasesAcct}
        title="Enable purchases"
        serverName={purchasesAcct?.name ?? ''}
        body={`Let the panel spend money through ${purchasesAcct?.name}: buying and changing IPs (including calls from IP-change API tokens) and placing server orders. Each purchase is recorded under Orders. The daily IP cap in Settings still applies.`}
        confirmLabel="Enable purchases"
        requireTyped
        confirming={busyId === purchasesAcct?.id}
        onConfirm={() => { if (purchasesAcct) void setPurchases(purchasesAcct, true); }}
        onClose={() => setPurchasesAcct(null)}
      />

      {addOpen && (
        <AddAccountDialog
          adapters={adapters}
          onClose={() => setAddOpen(false)}
          onDone={() => { setAddOpen(false); qc.invalidateQueries(); }}
        />
      )}
      <Toast msg={toast} onClose={() => setToast(null)} />
    </Stack>
  );
}

function AddAccountDialog({ adapters, onClose, onDone }: {
  adapters: { data?: AdapterInfo[]; isPending: boolean }; onClose: () => void; onDone: () => void;
}) {
  const list = adapters.data ?? [];
  const [adapter, setAdapter] = useState('');
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showToken, setShowToken] = useState(false);
  // cred-capable adapters only (a.source = catalog-only source) - the menu
  // offers exactly these; the default must be one of them, never a blank select
  const credList = list.filter(a => !a.source);
  const effAdapter = adapter || credList[0]?.key || '';
  // the adapter's own form: one "token" field, or several (a username +
  // password panel). Values are keyed per adapter so switching providers
  // never carries one provider's secret into another's form.
  const [values, setValues] = useState<Record<string, string>>({});
  const fields = credList.find(a => a.key === effAdapter)?.credential_fields ?? [];
  const multi = fields.length > 1 || (fields.length === 1 && fields[0].name !== 'token');
  const fieldValue = (f: CredentialField) => values[`${effAdapter}:${f.name}`] ?? f.default ?? '';
  const missing = multi ? fields.some(f => !fieldValue(f).trim()) : !token.trim();

  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post('/api/accounts', multi
        ? { adapter: effAdapter, name: name.trim(),
            fields: Object.fromEntries(fields.map(f => [f.name, fieldValue(f)])) }
        : { adapter: effAdapter, name: name.trim(), token: token.trim() });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog aria-labelledby="omni-dlg-212" open onClose={busy ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle id="omni-dlg-212">Add provider account</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ pt: 1 }}>
          <TextField select label="Provider" value={effAdapter}
                     onChange={e => setAdapter(e.target.value)} size="small" required
                     disabled={adapters.isPending}>
            {adapters.isPending && <MenuItem value="" disabled>Loading providers…</MenuItem>}
            {credList.map(a =>
              <MenuItem key={a.key} value={a.key}>{a.display_name}</MenuItem>)}
          </TextField>
          {effAdapter === 'hetzner' && (
            <Alert severity="info" icon={false}>
              Create a read/write token at console.hetzner.com → Security →
              API tokens, then paste it here.
            </Alert>
          )}
          {effAdapter === 'leaseweb' && (
            <Alert severity="info" icon={false}>
              Create an API key at secure.leaseweb.com → API → API keys
              (read/write for full panel features).
            </Alert>
          )}
          {effAdapter === 'ovh' && (
            <Alert severity="info" icon={false}>
              Paste your OVH credential as one string — API keys as
              applicationKey:applicationSecret:consumerKey
              (www.ovh.com/auth/api/createToken, rights on /cloud/project*
              and /vps*), or an OAuth2 service account as
              client_id:client_secret. Both cover VPS and Public Cloud.
            </Alert>
          )}
          {effAdapter === 'gcore' && (
            <Alert severity="info" icon={false}>
              Create an API token at gcore.com → account icon → Profile →
              API tokens (administrator or engineer role for full panel
              features), then paste it here.
            </Alert>
          )}
          {effAdapter === 'netlen' && (
            <Alert severity="info" icon={false}>
              Create an API key in the Netlen panel (API section) and add this
              panel's outgoing IP to the key's IP allowlist - Netlen refuses
              every call from any other address.
            </Alert>
          )}
          {effAdapter === 'tube' && (
            <Alert severity="info" icon={false}>
              Your tube-hosting.com login. Their API has no tokens; the panel
              signs in with e-mail + password (stored encrypted).
            </Alert>
          )}
          {effAdapter === 'lightnode' && (
            <Alert severity="info" icon={false}>
              Request an API token in the LightNode console (Account → Token
              list); LightNode issues it after review.
            </Alert>
          )}
          {effAdapter === 'gcore_hosting' && (
            <Alert severity="info" icon={false}>
              Your hosting.gcore.com panel login. The hosting panel
              (BILLmanager) has no API tokens, so the panel signs in with
              your username and password - stored encrypted, sent only to
              the panel URL over HTTPS.
            </Alert>
          )}
          <TextField label="Account name" value={name}
                     onChange={e => setName(e.target.value)} size="small" required
                     helperText="A label, e.g. main or edge" />
          {multi && fields.map(f => (
            <TextField key={`${effAdapter}:${f.name}`} label={f.label} size="small" required
                       type={f.secret && !showToken ? 'password' : 'text'}
                       value={fieldValue(f)}
                       onChange={e => setValues(v => ({ ...v, [`${effAdapter}:${f.name}`]: e.target.value }))}
                       helperText={f.help ?? (f.secret ? 'Stored encrypted, never shown again.' : undefined)}
                       slotProps={f.secret ? { input: { endAdornment: (
                         <InputAdornment position="end">
                           <IconButton size="small" edge="end"
                                       aria-label={showToken ? `Hide ${f.label}` : `Show ${f.label}`}
                                       onClick={() => setShowToken(s => !s)}>
                             {showToken ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                           </IconButton>
                         </InputAdornment>
                       ) } } : undefined} />
          ))}
          {!multi && <TextField label="API token" type={showToken ? 'text' : 'password'} value={token}
                     onChange={e => setToken(e.target.value)} size="small" required
                     helperText="Stored encrypted. Only the last 4 characters are ever shown."
                     slotProps={{ input: { endAdornment: (
                       <InputAdornment position="end">
                         <IconButton size="small" edge="end"
                                     aria-label={showToken ? 'Hide token' : 'Show token'}
                                     onClick={() => setShowToken(s => !s)}>
                           {showToken ? <VisibilityOffIcon fontSize="small" /> : <VisibilityIcon fontSize="small" />}
                         </IconButton>
                       </InputAdornment>
                     ) } }} />}
          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="contained"
                disabled={busy || !effAdapter || !name.trim() || missing || adapters.isPending}
                onClick={submit}>{busy ? 'Adding…' : 'Add'}</Button>
      </DialogActions>
    </Dialog>
  );
}
