import { useEffect, useState } from 'react';
import { NavLink, Route, Routes, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import AppBar from '@mui/material/AppBar';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItemButton from '@mui/material/ListItemButton';
import ListItemIcon from '@mui/material/ListItemIcon';
import ListItemText from '@mui/material/ListItemText';
import Toolbar from '@mui/material/Toolbar';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';
import SpaceDashboardIcon from '@mui/icons-material/SpaceDashboard';
import DnsIcon from '@mui/icons-material/Dns';
import StorefrontIcon from '@mui/icons-material/Storefront';
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong';
import CloudSyncIcon from '@mui/icons-material/CloudSync';
import KeyIcon from '@mui/icons-material/Key';
import ExtensionIcon from '@mui/icons-material/Extension';
import PeopleIcon from '@mui/icons-material/People';
import SettingsIcon from '@mui/icons-material/Settings';
import LogoutIcon from '@mui/icons-material/Logout';
import MenuIcon from '@mui/icons-material/Menu';
import RefreshIcon from '@mui/icons-material/Refresh';
import LightModeIcon from '@mui/icons-material/LightMode';
import DarkModeIcon from '@mui/icons-material/DarkMode';
import SearchIcon from '@mui/icons-material/Search';
import { api, post, subscribeStream } from './api';
import { CommandPalette } from './components/CommandPalette';
import { FleetView } from './views/FleetView';
import { OverviewView } from './views/OverviewView';
import { CatalogView } from './views/CatalogView';
import { OrdersView } from './views/OrdersView';
import { AllowancesView } from './views/AllowancesView';
import { CredentialsView } from './views/CredentialsView';
import { AdaptersView } from './views/AdaptersView';
import { UsersView } from './views/UsersView';
import { SettingsView } from './views/SettingsView';
import { LoginView } from './views/LoginView';
import type { FleetResponse } from './types';

const DRAWER_W = 240;

// Active nav: tinted brand surface + brand icon + weight, not a whisper.
const navSx = {
  mb: 0.25,
  '&.active': {
    bgcolor: 'rgba(94, 106, 210, 0.10)',
    '& .MuiListItemIcon-root': { color: 'primary.main' },
    '& .MuiListItemText-primary': { fontWeight: 600, color: 'primary.main' },
  },
};

function Shell({ user, themeMode, onToggleTheme }: {
  user: { id: number; username: string; role: string };
  themeMode: 'light' | 'dark';
  onToggleTheme: () => void;
}) {
  const qc = useQueryClient();
  const theme = useTheme();
  const navigate = useNavigate();
  const isDesktop = useMediaQuery(theme.breakpoints.up('md'));
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  const fleet = useQuery<FleetResponse>({
    queryKey: ['fleet'],
    queryFn: () => api<FleetResponse>('/api/fleet'),
  });

  // SSE -> invalidate (live values swap in place)
  useEffect(() => subscribeStream((ev) => {
    if (ev === 'servers_updated') qc.invalidateQueries({ queryKey: ['fleet'] });
    if (ev === 'catalog_updated') qc.invalidateQueries({ queryKey: ['catalog'] });
    if (ev === 'order') qc.invalidateQueries({ queryKey: ['orders'] });
  }), [qc]);

  // Ctrl+K command palette
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const errCount = Object.values(fleet.data?.sync ?? {}).filter(s => s.last_error).length;

  const NAV = [
    { to: '/overview', label: 'Overview', icon: <SpaceDashboardIcon /> },
    { to: '/fleet', label: 'Fleet', icon: <DnsIcon /> },
    { to: '/catalog', label: 'Catalog', icon: <StorefrontIcon /> },
    { to: '/orders', label: 'Orders', icon: <ReceiptLongIcon /> },
    { to: '/allowances', label: 'Allowances & billing', icon: <CloudSyncIcon /> },
    { to: '/credentials', label: 'Credentials', icon: <KeyIcon /> },
    { to: '/adapters', label: 'Adapters', icon: <ExtensionIcon /> },
  ];
  const ADMIN_NAV = [
    { to: '/users', label: 'Users', icon: <PeopleIcon /> },
    { to: '/settings', label: 'Settings', icon: <SettingsIcon /> },
  ];

  const drawer = (
    <List sx={{ pt: 1, px: 1 }}>
      {NAV.map(n => (
        <ListItemButton key={n.to} component={NavLink} to={n.to}
                        onClick={() => setDrawerOpen(false)}
                        sx={navSx}>
          <ListItemIcon sx={{ minWidth: 40 }}>{n.icon}</ListItemIcon>
          <ListItemText primary={n.label} slotProps={{ primary: { sx: { fontSize: 14 } } }} />
        </ListItemButton>
      ))}
      {user.role === 'admin' && (
        <>
          <Divider sx={{ my: 1 }} />
          {ADMIN_NAV.map(n => (
            <ListItemButton key={n.to} component={NavLink} to={n.to}
                            onClick={() => setDrawerOpen(false)}
                            sx={navSx}>
              <ListItemIcon sx={{ minWidth: 40 }}>{n.icon}</ListItemIcon>
              <ListItemText primary={n.label} slotProps={{ primary: { sx: { fontSize: 14 } } }} />
            </ListItemButton>
          ))}
        </>
      )}
    </List>
  );

  return (
    <Box sx={{ display: 'flex' }}>
      <AppBar position="fixed" color="inherit" elevation={0}
              sx={{ borderBottom: 1, borderColor: 'divider', zIndex: t => t.zIndex.drawer + 1 }}>
        <Toolbar sx={{ minHeight: 56, gap: 1 }}>
          {!isDesktop && (
            <IconButton size="small" edge="start" onClick={() => setDrawerOpen(true)}
                         aria-label="Open navigation">
              <MenuIcon fontSize="small" />
            </IconButton>
          )}
          <Typography variant="h6" sx={{ mr: 2, letterSpacing: '-0.02em', fontWeight: 600 }}>
            OmniCloud
          </Typography>
          <Box sx={{ flex: 1 }} />
          <Button size="small" startIcon={<SearchIcon />} onClick={() => setPaletteOpen(true)}
                  sx={{ color: 'text.secondary', mr: 1 }}>
            Search
            <Typography className="num" variant="caption" sx={{ ml: 1, color: 'text.secondary',
              border: 1, borderColor: 'divider', borderRadius: 1, px: 0.75 }}>
              Ctrl K
            </Typography>
          </Button>
          {errCount > 0 && (
            <Tooltip title={Object.entries(fleet.data?.sync ?? {})
              .filter(([, s]) => s.last_error)
              .map(([id, s]) => `Account ${id}: ${s.last_error}`).join('\n')}>
              <Chip size="small" color="warning" variant="outlined"
                    label={`${errCount} sync error${errCount > 1 ? 's' : ''}`} />
            </Tooltip>
          )}
          <Button size="small" startIcon={<RefreshIcon />}
                  onClick={() => qc.invalidateQueries()} disabled={fleet.isFetching}>
            Refresh
          </Button>
          <IconButton size="small" onClick={onToggleTheme}
                      aria-label="Toggle dark mode">
            {themeMode === 'dark' ? <LightModeIcon fontSize="small" /> : <DarkModeIcon fontSize="small" />}
          </IconButton>
          <Chip size="small" variant="outlined" label={`${user.username} · ${user.role}`} />
          <IconButton size="small" onClick={() => post('/api/auth/logout').then(() => location.reload())}
                      aria-label="Sign out">
            <LogoutIcon fontSize="small" />
          </IconButton>
        </Toolbar>
      </AppBar>

      {isDesktop ? (
        <Drawer variant="permanent" sx={{ width: DRAWER_W, flexShrink: 0,
          '& .MuiDrawer-paper': { width: DRAWER_W, boxSizing: 'border-box', pt: '64px' } }}>
          {drawer}
        </Drawer>
      ) : (
        <Drawer variant="temporary" open={drawerOpen} onClose={() => setDrawerOpen(false)}
                sx={{ '& .MuiDrawer-paper': { width: DRAWER_W, pt: '64px' } }}>
          {drawer}
        </Drawer>
      )}

      <Box component="main" sx={{ flexGrow: 1, p: 3, pt: '72px', maxWidth: 1600, minWidth: 0 }}>
        <Routes>
          <Route path="/" element={<OverviewView />} />
          <Route path="/overview" element={<OverviewView />} />
          <Route path="/fleet" element={<FleetView />} />
          <Route path="/catalog" element={<CatalogView />} />
          <Route path="/orders" element={<OrdersView />} />
          <Route path="/allowances" element={<AllowancesView />} />
          <Route path="/credentials" element={<CredentialsView />} />
          <Route path="/adapters" element={<AdaptersView />} />
          {user.role === 'admin' && <Route path="/users" element={<UsersView />} />}
          {user.role === 'admin' && <Route path="/settings" element={<SettingsView />} />}
          <Route path="*" element={<Navigate2 to="/overview" />} />
        </Routes>
      </Box>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)}
                       onNavigate={to => { navigate(to); setPaletteOpen(false); }} />
    </Box>
  );
}

// tiny wrapper to keep the import list tidy
function Navigate2({ to }: { to: string }) {
  const nav = useNavigate();
  useEffect(() => { nav(to, { replace: true }); }, [nav, to]);
  return null;
}

export default function App({ themeMode, onToggleTheme }: {
  themeMode: 'light' | 'dark';
  onToggleTheme: () => void;
}) {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: () => api<{ id: number; username: string; role: string }>('/api/auth/me'),
    retry: false,
  });
  const status = useQuery({
    queryKey: ['auth-status'],
    queryFn: () => api<{ needs_setup: boolean }>('/api/auth/status'),
    retry: false,
  });

  if (me.isPending || status.isPending) return null;
  if (me.isError) {
    return <LoginView needsSetup={!!status.data?.needs_setup} />;
  }
  return <Shell user={me.data!} themeMode={themeMode} onToggleTheme={onToggleTheme} />;
}
