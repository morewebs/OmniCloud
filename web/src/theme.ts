import { createTheme } from '@mui/material/styles';

/**
 * OmniCloud v2 theme: custom-branded Material, Modern SaaS bar.
 * - ONE brand accent: Indigo (primary buttons, active nav, links, focus,
 *   selection). Surfaces stay neutral Zinc; status trio stays desaturated.
 * - Dark mode via MUI cssVariables + useColorScheme; localStorage key
 *   omnicloud-theme (default system), toggle in the AppBar.
 * - Numbers use tabular-nums mono via the .num class (CssBaseline).
 */

const BRAND = {
  light: '#5E6AD2',
  dark: '#7C7CF0',
  hover: '#4F4FC7',
  hoverDark: '#6A6AE4',
};

// Light palette (design.md Zinc axis)
const lightPalette = {
  mode: 'light' as const,
  primary: { main: BRAND.light, dark: BRAND.hover, light: '#8089DB' },
  secondary: { main: '#3F3F46' },
  success: { main: '#3F6212' },
  warning: { main: '#854D0E' },
  error: { main: '#991B1B' },
  background: { default: '#FAFAFA', paper: '#FFFFFF' },
  divider: '#E4E4E7',
  text: { primary: '#18181B', secondary: '#71717A' },
};

// Dark palette (design.md's own symmetric mapping, statuses lightened one step)
const darkPalette = {
  mode: 'dark' as const,
  primary: { main: BRAND.dark, dark: BRAND.hoverDark, light: '#9D9DF5' },
  secondary: { main: '#A1A1AA' },
  success: { main: '#A3E635' },
  warning: { main: '#FBBF24' },
  error: { main: '#F87171' },
  background: { default: '#09090B', paper: '#18181B' },
  divider: '#3F3F46',
  text: { primary: '#FAFAFA', secondary: '#A1A1AA' },
};

const shared = {
  cssVariables: true,
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: '"Geist", "Inter", "Helvetica Neue", Arial, sans-serif',
    h1: { fontSize: '2.25rem', fontWeight: 600, letterSpacing: '-0.02em' },
    h2: { fontSize: '1.75rem', fontWeight: 600, letterSpacing: '-0.02em' },
    h3: { fontSize: '1.375rem', fontWeight: 600, letterSpacing: '-0.01em' },
    h4: { fontSize: '1.125rem', fontWeight: 600 },
    h5: { fontSize: '1.0625rem', fontWeight: 600 },
    h6: { fontSize: '1rem', fontWeight: 600 },
    subtitle1: { fontSize: '0.9375rem', fontWeight: 500 },
    subtitle2: { fontSize: '0.8125rem', fontWeight: 500 },
    body1: { fontSize: '0.9375rem' },
    body2: { fontSize: '0.875rem' },
    caption: { fontSize: '0.75rem' },
    overline: { fontSize: '0.6875rem', fontWeight: 600, letterSpacing: '0.08em' },
    button: { textTransform: 'none' as const, fontWeight: 500 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        '.num, .num input': {
          fontFamily: '"Geist Mono", ui-monospace, "Cascadia Mono", Consolas, monospace',
          fontVariantNumeric: 'tabular-nums',
        },
        // one global focus ring, visible on every surface in both themes
        '*:focus-visible': {
          outline: '2px solid var(--mui-palette-primary-main)',
          outlineOffset: 2,
        },
        '@media (prefers-reduced-motion: reduce)': {
          '*': {
            animationDuration: '0.01ms !important',
            animationIterationCount: '1 !important',
            transitionDuration: '0.01ms !important',
          },
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 8 },
        sizeSmall: { padding: '4px 10px', fontSize: '0.8125rem' },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 10,
          border: '1px solid',
          borderColor: 'divider',
          boxShadow: 'none',
        },
      },
    },
    MuiChip: { styleOverrides: { root: { borderRadius: 6 } } },
    MuiDialog: { styleOverrides: { paper: { borderRadius: 12 } } },
    MuiOutlinedInput: { styleOverrides: { root: { borderRadius: 8 } } },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: 'var(--mui-palette-background-default)',
          color: 'var(--mui-palette-text-primary)',
          fontSize: '0.75rem',
          borderRadius: 6,
          border: '1px solid var(--mui-palette-divider)',
          boxShadow: '0 4px 16px rgba(0,0,0,0.16)',
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: { root: { height: 6, borderRadius: 3 } },
    },
    MuiListItemButton: {
      styleOverrides: { root: { borderRadius: 8 } },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { padding: '8px 12px', borderColor: 'divider' },
        head: { fontWeight: 600, fontSize: '0.75rem', color: 'text.secondary' },
      },
    },
  },
};

export const lightTheme = createTheme({ ...shared, palette: lightPalette });
export const darkTheme = createTheme({ ...shared, palette: darkPalette });
