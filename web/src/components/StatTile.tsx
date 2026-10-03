import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

/** Dashboard stat tile: value (mono), label, optional delta + sub.
 *  Delta color is neutral by default — rising traffic on an overage-risk
 *  panel is not "good"; callers pass deltaGood when up truly is better. */
export function StatTile({ label, value, unit, delta, sub, dominant, deltaGood }: {
  label: string;
  value: string;
  unit?: string;
  delta?: { text: string; up: boolean } | null;
  sub?: string;
  dominant?: boolean;
  deltaGood?: boolean;
}) {
  return (
    <Card sx={{ flex: dominant ? 2 : 1, minWidth: 0 }}>
      <CardContent sx={{ py: 2, px: 2.5 }}>
        <Typography variant="subtitle2" sx={{ color: 'text.secondary' }}>
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
              sx={{
                color: deltaGood
                  ? (delta.up ? 'success.main' : 'text.secondary')
                  : (delta.up ? 'warning.main' : 'success.main'),
              }}
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
