import { useQuery } from '@tanstack/react-query';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import TableBody from '@mui/material/TableBody';
import TableCell from '@mui/material/TableCell';
import TableHead from '@mui/material/TableHead';
import TableRow from '@mui/material/TableRow';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import { api } from '../api';
import type { AccountRow, AdapterInfo } from '../types';
import { CAPABILITY_LABELS } from '../types';
import { PageHeader } from '../components/PageHeader';
import { usePageTitle } from '../usePageTitle';

/** All 7 providers: fleet adapters + catalog providers in one registry view. */
export function AdaptersView() {
  usePageTitle('Adapters');
  const adapters = useQuery<AdapterInfo[]>({ queryKey: ['adapters'],
    queryFn: () => api<AdapterInfo[]>('/api/adapters') });
  const accounts = useQuery<AccountRow[]>({ queryKey: ['accounts'],
    queryFn: () => api<AccountRow[]>('/api/accounts') });

  if (adapters.isPending || accounts.isPending) return null;
  if (adapters.isError) return <Alert severity="error">{(adapters.error as Error).message}</Alert>;
  // an accounts failure must not throw in render (ErrorBoundary) - degrade
  // to "unknown account count" instead
  if (accounts.isError) return <Alert severity="error">{(accounts.error as Error).message}</Alert>;

  const fleet = adapters.data!.filter(a => !a.source);
  const catalogOnly = adapters.data!.filter(a => a.source);

  return (
    <Stack spacing={3}>
      <PageHeader title="Adapters"
        subtitle="Every provider on the panel — what it can do here, and where its plan data comes from." />

      <Box sx={{ overflowX: 'auto' }}>
        <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Provider</TableCell>
            <TableCell>Server management</TableCell>
            <TableCell>Plan catalog</TableCell>
            <TableCell>Accounts</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {[...fleet, ...catalogOnly].map(a => {
            const users = accounts.data!.filter(x => x.adapter === a.key);
            return (
              <TableRow key={a.key}>
                <TableCell>{a.display_name}</TableCell>
                <TableCell>
                  {a.capabilities.length === 0 ? (
                    <Tooltip title={CATALOG_NOTES[a.key] ?? 'Catalog only'}>
                      <Chip size="small" label="catalog only" />
                    </Tooltip>
                  ) : (
                    <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5 }}>
                      {a.capabilities.map(c => (
                        <Chip key={c} size="small" variant="outlined"
                              label={CAPABILITY_LABELS[c] ?? c} />
                      ))}
                    </Stack>
                  )}
                </TableCell>
                <TableCell>
                  {a.source ? <CatalogSourceChip source={a.source} /> : (
                    <Chip size="small" variant="outlined" label="live API" />
                  )}
                </TableCell>
                <TableCell>
                  {users.length === 0 ? '—' : users.map(u => u.name).join(', ')}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      </Box>

      <Stack spacing={1} sx={{ maxWidth: 720 }}>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          <strong>Fleet adapters</strong> manage servers through the provider API with
          your account credentials. <strong>Catalog providers</strong> feed the plan
          marketplace: live ones fetch prices straight from the provider, curated
          ones carry public list prices with a last-verified date.
        </Typography>
        {catalogOnly.map(a => CATALOG_NOTES[a.key] && (
          <Typography key={a.key} variant="body2" sx={{ color: 'text.secondary' }}>
            {a.display_name}: {CATALOG_NOTES[a.key]}
          </Typography>
        ))}
        <Typography variant="body2" sx={{ color: 'text.secondary', mt: 1 }}>
          Adding a provider = one file in <code>server/adapters/</code> implementing
          the contract in <code>base.py</code>. See <code>docs/provider-truth.md</code>
          for why some providers are catalog-only.
        </Typography>
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Building on top of the panel? Every route here is also a JSON API:{' '}
          <a href="/api/docs" target="_blank" rel="noreferrer">interactive docs</a>,
          or with a token from Settings:
        </Typography>
        <Box component="pre" className="num"
             sx={{ m: 0, p: 1.5, fontSize: 12, bgcolor: 'action.hover', borderRadius: 1,
                   overflowX: 'auto' }}>
{`curl -H "Authorization: Bearer <token>" \\
  http://localhost:8000/api/fleet`}
        </Box>
      </Stack>
    </Stack>
  );
}

function CatalogSourceChip({ source }: { source: 'live' | 'seeded' }) {
  return source === 'live'
    ? <Chip size="small" color="success" variant="outlined" label="live API" />
    : <Chip size="small" color="warning" variant="outlined" label="curated" />;
}

// Why each catalog-only provider isn't a fleet adapter (docs/provider-truth.md)
const CATALOG_NOTES: Record<string, string> = {
  ovh: 'Fleet management (VPS + Public Cloud) is available — add an OVH credential in Credentials. Plans come from the public order catalog, no credential needed.',
  gcore: 'Plans from the public API; server management needs an API key.',
  tube: 'Plans from their pricing-page data asset; no public API.',
  netlen: 'API needs Bearer + IP allowlist; public list prices curated.',
  lightnode: 'No public API; public list prices curated.',
};
