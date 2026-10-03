import { createTheme } from '@mui/material/styles';

// Material 3 standard light theme, neutral primary, desaturated status trio
// (design.md status semantics mapped onto Material slots; no provider hues).
// Numbers everywhere use tabular-nums via the .num global class.
const theme = createTheme({
  cssVariables: true,
  palette: {
    mode: 'light',
    primary: { main: '#18181B' },        // charcoal ink - the interaction accent
    secondary: { main: '#3F3F46' },     // graphite
    success: { main: '#3F6212' },      // running green (desaturated)
    warning: { main: '#854D0E' },      // warning amber (desaturated)
    error: { main: '#991B1B' },        // down red (desaturated)
    background: { default: '#FAFAFA', paper: '#FFFFFF' },
    divider: '#E4E4E7',
    text: { primary: '#18181B', secondary: '#71717A' },
  },
  shape: { borderRadius: 8 },
  typography: {
    fontFamily: '"Geist", "Inter", "Helvetica Neue", Arial, sans-serif',
    button: { textTransform: 'none' },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        '.num, .num input': {
          fontFamily: '"Geist Mono", ui-monospace, "Cascadia Mono", Consolas, monospace',
          fontVariantNumeric: 'tabular-nums',
        },
        '@media (prefers-reduced-motion: reduce)': {
          '*': { animationDuration: '0.01ms !important', transitionDuration: '0.01ms !important' },
        },
      },
    },
    MuiButton: { defaultProps: { disableElevation: true } },
    MuiLinearProgress: { styleOverrides: { root: { height: 6, borderRadius: 3 } } },
  },
});

export default theme;
