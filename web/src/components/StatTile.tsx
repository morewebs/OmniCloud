import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

/** Dashboard stat tile: value (mono), label, optional delta + sub. */
export function StatTile({ label, value, unit, delta, sub, dominant }: {
  label: string;
  value: string;
  unit?: string;
  delta?: { text: string; up: boolean } | null;
  sub?: string;
  dominant?: boolean;
}) {
  return (
    <Card sx={{ flex: dominant ? 2 : 1, minWidth: 0 }}>
      <CardContent sx={{ py: 2, px: 2.5 }}>
        <Typography variant="overline" sx={{ color: 'text.secondary', lineHeight: 1.8 }}>
          {label}
        </Typography>
        <Stack direction="row" spacing={1} sx={{ alignItems: 'baseline' }}>
          <Typography className="num" variant={dominant ? 'h2' : 'h3'} component="div">
            {value}
          </Typography>
          {unit && (
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>{unit}</Typography>
          )}
          {delta && (
            <Typography
              className="num"
              variant="caption"
              sx={{ color: delta.up ? 'success.main' : 'text.secondary' }}
            >
              {delta.text}
            </Typography>
          )}
        </Stack>
        {sub && (
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>{sub}</Typography>
        )}
      </CardContent>
    </Card>
  );
}
