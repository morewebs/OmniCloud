import { useMemo, useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import InputAdornment from '@mui/material/InputAdornment';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemText from '@mui/material/ListItemText';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import SearchIcon from '@mui/icons-material/Search';

export interface FwRule {
  direction: 'in' | 'out';
  protocol: 'tcp' | 'udp' | 'icmp';
  port: string; // "" = any, "22" or "80,443" or "60000-61000"
  source_ips: string; // "" = any, comma-separated CIDRs
}

export interface FirewallRow {
  id: number;
  name: string;
  rules: number;
  applied_to_count: number;
}

/**
 * Firewall management optimized for real fleets: Hetzner firewalls are SHARED
 * resources applied to batches of servers, and a firewall can carry 100+
 * rules. So the primary workflow is attach/detach an existing firewall
 * (searchable list - fleets have many), not rule editing. Creating a new
 * firewall with rules is a collapsed secondary path for fresh servers.
 */
export function FirewallDialog({ open, serverName, attachedFirewalls, onDetach, onShowCreate, onClose, loading, error }: {
  open: boolean;
  serverName: string;
  /** firewalls currently attached to this server (id, name). */
  attachedFirewalls: { id: number; name: string }[];
  onDetach: (firewallId: number) => Promise<void> | void;
  onShowCreate: () => void;
  onClose: () => void;
  loading?: boolean;
  error?: string | null;
}) {
  const [search, setSearch] = useState('');
  const [confirmId, setConfirmId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const filtered = useMemo(
    () => attachedFirewalls.filter(f => !search || f.name.toLowerCase().includes(search)),
    [attachedFirewalls, search]);

  const detach = async (id: number) => {
    setBusy(true);
    try {
      await onDetach(id);
      setConfirmId(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog aria-labelledby="omni-dlg-72" open={open} onClose={busy ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle id="omni-dlg-72">Firewall - {serverName}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <Alert severity="info" icon={false}>
            Firewalls are shared across batches of servers. Detaching removes
            only this server from the firewall; other servers keep theirs.
          </Alert>

          {loading && <LinearProgress />}
          {error && <Alert severity="error">{error}</Alert>}

          <TextField
            size="small" placeholder="Search attached firewalls"
            aria-label="Search attached firewalls"
            value={search} onChange={e => setSearch(e.target.value)}
            slotProps={{ input: { startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ) } }}
          />

          <Box>
            <Typography variant="overline" sx={{ display: 'block', lineHeight: 2 }}>
              Attached ({filtered.length})
            </Typography>
            <List dense disablePadding sx={{ maxHeight: 300, overflowY: 'auto' }}>
              {filtered.length === 0 && !loading && !error && (
                <Typography variant="body2" sx={{ color: 'text.secondary', px: 2, py: 1 }}>
                  {search ? 'No attached firewalls match the search.'
                    : 'No firewalls attached. This server accepts all traffic.'}
                </Typography>
              )}
              {filtered.map(f => (
                <ListItem key={f.id} disableGutters
                  secondaryAction={
                    confirmId === f.id ? (
                      <Stack direction="row" spacing={0.5}>
                        <Button size="small" color="error" variant="contained"
                                disabled={busy}
                                onClick={() => detach(f.id)}>
                          {busy ? 'Detaching…' : `Confirm — remove ${f.name}`}
                        </Button>
                        <Button size="small" disabled={busy}
                                onClick={() => setConfirmId(null)}>Keep</Button>
                      </Stack>
                    ) : (
                      <Button size="small" color="error" disabled={busy}
                              onClick={() => setConfirmId(f.id)}>
                        Detach
                      </Button>
                    )
                  }>
                  <ListItemText
                    primary={f.name}
                    secondary={confirmId === f.id
                      ? 'This opens the server to all traffic' : undefined}
                    slotProps={{ primary: { sx: { fontSize: 14, fontWeight: 500 } } }}
                  />
                </ListItem>
              ))}
            </List>
          </Box>

          <Button onClick={onShowCreate} disabled={busy}>Create a new firewall…</Button>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={busy}>Close</Button>
      </DialogActions>
    </Dialog>
  );
}

/** Attach workflow: pick from the account's existing firewalls (searchable -
 * a large fleet has many, and each carries its own rule set shown as chips). */
export function AttachFirewallDialog({ open, serverName, firewalls, excludeIds, onAttach, onClose, loading, error }: {
  open: boolean;
  serverName: string;
  firewalls: FirewallRow[];
  excludeIds: number[];
  onAttach: (firewallId: number) => Promise<void> | void;
  onClose: () => void;
  loading?: boolean;
  error?: string | null;
}) {
  const [search, setSearch] = useState('');
  const exclude = new Set(excludeIds);
  const filtered = useMemo(
    () => firewalls
      .filter(f => !exclude.has(f.id))
      .filter(f => !search || f.name.toLowerCase().includes(search))
      .sort((a, b) => a.name.localeCompare(b.name)),
    [firewalls, exclude, search]);

  return (
    <Dialog aria-labelledby="omni-dlg-167" open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle id="omni-dlg-167">Attach firewall to {serverName}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          {loading && <LinearProgress />}
          {error && <Alert severity="error">{error}</Alert>}
          <TextField
            size="small" placeholder="Search firewalls" fullWidth autoFocus
            aria-label="Search firewalls"
            value={search} onChange={e => setSearch(e.target.value)}
            slotProps={{ input: { startAdornment: (
              <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment>
            ) } }}
          />
          <List dense disablePadding sx={{ maxHeight: 400, overflowY: 'auto' }}>
            {filtered.length === 0 && !loading && (
              <Typography variant="body2" sx={{ color: 'text.secondary', px: 2, py: 1 }}>
                {search ? 'No firewalls match the search.' : 'No other firewalls on this account.'}
              </Typography>
            )}
            {filtered.map(f => (
              <ListItemButton key={f.id} onClick={() => onAttach(f.id)} disableGutters sx={{ borderRadius: 1 }}>
                <ListItemText
                  primary={f.name}
                  secondary={
                    <Stack direction="row" spacing={1} sx={{ mt: 0.5 }}>
                      <Chip size="small" variant="outlined" className="num" label={`${f.rules} rules`} />
                      <Chip size="small" variant="outlined" className="num" label={`${f.applied_to_count} servers`} />
                    </Stack>
                  }
                  slotProps={{ primary: { sx: { fontSize: 14, fontWeight: 500 } } }}
                />
              </ListItemButton>
            ))}
          </List>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
}

/** Create-new path: for a fresh server without any firewall. Kept minimal
 * (a handful of allow rules); bulk rule management belongs to the provider
 * console when a firewall already carries 100+ rules. */
const PORT_RE = /^(\d{1,5}(-\d{1,5})?)(,\d{1,5}(-\d{1,5})?)*$/;
const CIDR_RE = /^\d{1,3}(\.\d{1,3}){3}(\/\d{1,2})?$/;

/** port list/range is well-formed AND every number is 0..65535 */
function portOk(port: string): boolean {
  if (!port) return true; // "any"
  if (!PORT_RE.test(port)) return false;
  return port.split(',').every(part => {
    const [a, b] = part.split('-').map(Number);
    return a <= 65535 && (b === undefined || (b <= 65535 && b > a));
  });
}

/** each comma-separated CIDR is 4 octets 0-255 (+ prefix 0-32 if present) */
function cidrOk(source: string): boolean {
  if (!source.trim()) return true; // "any"
  return source.split(',').map(s => s.trim()).every(c => {
    if (!c) return false;
    const [ip, prefix] = c.split('/');
    if (!CIDR_RE.test(c)) return false;
    const octets = ip.split('.').map(Number);
    if (octets.some(o => o > 255)) return false;
    return prefix === undefined || (Number(prefix) <= 32);
  });
}

export function CreateFirewallDialog({ open, serverName, onCreate, onClose, busy, error }: {
  open: boolean;
  serverName: string;
  onCreate: (name: string, rules: FwRule[]) => Promise<void> | void;
  onClose: () => void;
  busy?: boolean;
  error?: string | null;
}) {
  const [name, setName] = useState(`${serverName}-fw`);
  const [rules, setRules] = useState<FwRule[]>([
    { direction: 'in', protocol: 'tcp', port: '22', source_ips: '' },
  ]);

  const set = (i: number, patch: Partial<FwRule>) =>
    setRules(rs => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const hasInbound = rules.some(r => r.direction === 'in');
  const portsValid = rules.every(r => portOk(r.port));
  const cidrsValid = rules.every(r => cidrOk(r.source_ips));

  return (
    <Dialog aria-labelledby="omni-dlg-261" open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle id="omni-dlg-261">Create firewall for {serverName}</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2}>
          <TextField size="small" label="Firewall name" value={name}
                     onChange={e => setName(e.target.value)} sx={{ maxWidth: 280 }} />
          <Alert severity="warning">
            Hetzner firewalls are stateful allow-lists: a firewall with no
            inbound allow rule drops all inbound traffic (outbound stays
            open). Start with an SSH allow rule.
          </Alert>
          {error && <Alert severity="error">{error}</Alert>}
          {rules.map((r, i) => (
            <Stack key={i} direction="row" sx={{ alignItems: 'center', flexWrap: 'wrap' }} spacing={1}>
              <TextField select label="Direction" value={r.direction}
                         onChange={e => set(i, { direction: e.target.value as FwRule['direction'] })}
                         sx={{ width: 110 }} size="small">
                <MenuItem value="in">in</MenuItem>
                <MenuItem value="out">out</MenuItem>
              </TextField>
              <TextField select label="Protocol" value={r.protocol}
                         onChange={e => set(i, { protocol: e.target.value as FwRule['protocol'] })}
                         sx={{ width: 110 }} size="small">
                <MenuItem value="tcp">tcp</MenuItem>
                <MenuItem value="udp">udp</MenuItem>
                <MenuItem value="icmp">icmp</MenuItem>
              </TextField>
              <TextField label="Port" value={r.port} onChange={e => set(i, { port: e.target.value })}
                         sx={{ width: 130 }} size="small" placeholder="any"
                         disabled={r.protocol === 'icmp'}
                         error={!portsValid}
                         helperText={!portsValid ? 'e.g. 22 or 80,443 or 60000-61000 (max 65535)' : undefined} />
              <TextField label="Source IPs" value={r.source_ips}
                         onChange={e => set(i, { source_ips: e.target.value })}
                         sx={{ width: 220 }} size="small" placeholder="any, or CIDRs"
                         error={!cidrsValid}
                         helperText={!cidrsValid ? 'e.g. 203.0.113.7 or 203.0.113.0/24' : undefined} />
              <Button size="small" onClick={() => setRules(rs => rs.filter((_, j) => j !== i))}>
                Remove
              </Button>
            </Stack>
          ))}
          <Stack direction="row" spacing={1}>
            <Button size="small"
                    onClick={() => setRules(rs => [...rs, { direction: 'in', protocol: 'tcp', port: '', source_ips: '' }])}>
              Add rule
            </Button>
          </Stack>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button size="small" variant="contained" onClick={() => onCreate(name, rules)}
                disabled={busy || !hasInbound || !portsValid || !cidrsValid || !name.trim()}>
          {busy ? 'Creating…' : 'Create and attach'}
        </Button>
        {!hasInbound && (
          <Typography variant="caption" sx={{ color: 'text.secondary', alignSelf: 'center', mr: 1 }}>
            add at least one inbound rule — no inbound rule makes the server unreachable
          </Typography>
        )}
      </DialogActions>
    </Dialog>
  );
}

/** Frontend rule shape -> Hetzner API rule shape. */
export function toHetznerRules(rules: FwRule[]): Record<string, unknown>[] {
  return rules.map(r => ({
    direction: r.direction,
    protocol: r.protocol,
    port: r.port || undefined,
    source_ips: r.source_ips ? r.source_ips.split(',').map(s => s.trim()).filter(Boolean) : [],
  }));
}
