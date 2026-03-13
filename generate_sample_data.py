"""Generate sample data for testing the Kinetic Viewer.

The metadata CSV is written with all required columns pre-populated:
  Strain_ID, Class, additive, Run_number, PATH_TO_KINETIC_DATA,
  flag, peak_od_time, auc_start, auc_value, gr_start, growth_rate
"""
import os, csv, math, random, numpy as np

os.makedirs('sample_data/kinetics', exist_ok=True)

AUC_START = 5.0   # default AUC integration start (hours)
GR_START  = 2.0   # default growth-rate fitting window start (hours)

def trapz_auc(times, values, start_t):
    """Trapezoidal AUC from start_t onwards."""
    times   = np.array(times,  dtype=float)
    values  = np.array(values, dtype=float)
    mask    = times >= start_t
    if not mask.any():
        return 0.0
    idx = np.argmax(mask)
    if idx > 0 and times[idx] > start_t:
        y_interp = values[idx-1] + (values[idx]-values[idx-1]) * \
                   (start_t - times[idx-1]) / (times[idx]-times[idx-1])
        ts = np.concatenate([[start_t], times[idx:]])
        vs = np.concatenate([[y_interp], values[idx:]])
    else:
        ts, vs = times[mask], values[mask]
    return float(np.trapezoid(vs, ts))


def specific_growth_rate(times, values, start_t):
    """
    Specific growth rate μ (h⁻¹): slope of ln(OD) vs time from start_t
    to the peak OD, fitted by least-squares linear regression.
    Returns 0.0 if there are fewer than 2 positive data points in the window.
    """
    times  = np.array(times,  dtype=float)
    values = np.array(values, dtype=float)
    peak_i = int(np.argmax(values))
    mask   = (times >= start_t) & (times <= times[peak_i]) & (values > 0)
    if mask.sum() < 2:
        return 0.0
    t_seg   = times[mask]
    ln_y    = np.log(values[mask])
    # least-squares: ln(OD) = μ·t + b  →  slope = μ
    slope, _ = np.polyfit(t_seg, ln_y, 1)
    return float(slope)


random.seed(42)
strains   = ['WT', 'MutA', 'MutB', 'OE1']
classes   = ['control', 'treatment']
additives = ['none', 'glucose']
runs      = [1, 2]

FIELDNAMES = [
    'Strain_ID', 'Class', 'additive', 'Run_number',
    'PATH_TO_KINETIC_DATA',
    'flag', 'peak_od_time', 'auc_start', 'auc_value',
    'gr_start', 'growth_rate',
]

rows = []
for strain in strains:
    for cls in classes:
        for additive in additives:
            for run in runs:
                fname = f"{strain}_{cls}_{additive}_run{run}.csv"
                fpath = os.path.abspath(f"sample_data/kinetics/{fname}")

                # ── generate kinetic data ──
                base   = random.uniform(0.05, 0.15)
                times  = list(range(0, 25))
                values = []
                for t in times:
                    noise = random.gauss(0, 0.01)
                    od    = base + (1.8 - base) / (1 + math.exp(-0.4*(t-8))) + noise
                    values.append(round(max(od, 0), 4))

                with open(fpath, 'w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['Time_h', 'OD600'])
                    for t, v in zip(times, values):
                        w.writerow([t, v])

                # ── compute metadata values from the kinetic data ──
                peak_idx      = int(np.argmax(values))
                peak_od_time  = float(times[peak_idx])
                auc_value     = round(trapz_auc(times, values, AUC_START), 4)
                gr_value      = round(specific_growth_rate(times, values, GR_START), 4)

                rows.append({
                    'Strain_ID':          strain,
                    'Class':              cls,
                    'additive':           additive,
                    'Run_number':         run,
                    'PATH_TO_KINETIC_DATA': fpath,
                    'flag':               '',
                    'peak_od_time':       peak_od_time,
                    'auc_start':          AUC_START,
                    'auc_value':          auc_value,
                    'gr_start':           GR_START,
                    'growth_rate':        gr_value,
                })

with open('sample_data/metadata.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=FIELDNAMES)
    w.writeheader()
    w.writerows(rows)

print(f"Generated {len(rows)} rows → sample_data/metadata.csv")
print(f"Columns: {', '.join(FIELDNAMES)}")
