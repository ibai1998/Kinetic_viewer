import json
import os
import numpy as np
import pandas as pd
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

FLAG_COLUMN   = 'flag'
FLAG_VALUES   = ['', 'good', 'bad', 'check']
COMPUTED_COLS = ['peak_od_time', 'auc_start', 'auc_value', 'gr_start', 'growth_rate']
DEFAULT_AUC_START = 5.0
DEFAULT_GR_START  = 2.0

_metadata_cache = {}


def _derive_filter_cols(df):
    """
    Return filter columns: Strain_ID plus every column that sits between
    Strain_ID and PATH_TO_KINETIC_DATA in the CSV (exclusive of the path col).
    Strain_ID is always first and always included even if the slice is empty.
    """
    cols = list(df.columns)
    if 'Strain_ID' not in cols:
        return []
    start = cols.index('Strain_ID')
    end   = cols.index('PATH_TO_KINETIC_DATA') if 'PATH_TO_KINETIC_DATA' in cols else len(cols)
    return cols[start:end]   # Strain_ID … up to (not including) PATH_TO_KINETIC_DATA


def index(request):
    return render(request, 'viewer/index.html')


# ── helpers ───────────────────────────────────────────────────────────────────

def _trapz_auc(xs, ys, start_t):
    """Trapezoidal AUC from start_t to end of data."""
    xs, ys = np.array(xs, dtype=float), np.array(ys, dtype=float)
    mask = xs >= start_t
    if not mask.any():
        return 0.0
    # interpolate left edge if start_t falls between two points
    idx = np.argmax(mask)
    if idx > 0 and xs[idx] > start_t:
        y_interp = ys[idx-1] + (ys[idx]-ys[idx-1]) * (start_t - xs[idx-1]) / (xs[idx]-xs[idx-1])
        xs_seg = np.concatenate([[start_t], xs[idx:]])
        ys_seg = np.concatenate([[y_interp], ys[idx:]])
    else:
        xs_seg, ys_seg = xs[mask], ys[mask]
    return float(np.trapezoid(ys_seg, xs_seg))


def _growth_rate(xs, ys, start_t):
    """
    Specific growth rate μ (h⁻¹): slope of ln(OD) vs time from start_t
    to the peak OD, fitted by least-squares linear regression.
    """
    xs   = np.array(xs, dtype=float)
    ys   = np.array(ys, dtype=float)
    peak_i = int(np.argmax(ys))
    mask   = (xs >= start_t) & (xs <= xs[peak_i]) & (ys > 0)
    if mask.sum() < 2:
        return 0.0
    slope, _ = np.polyfit(xs[mask], np.log(ys[mask]), 1)
    return float(slope)
    """Persist the in-memory df back to disk (without internal columns)."""
    df       = _metadata_cache['df']
    filepath = _metadata_cache['filepath']
    save_df  = df.drop(columns=['_row_id'], errors='ignore')
    save_df.to_csv(filepath, index=False)


def _build_mask(df, filters, prefix='', empty_matches_all=False):
    filter_cols = _metadata_cache.get('filter_cols', [])
    active = {col: filters.get(f'{prefix}{col}')
              for col in filter_cols
              if filters.get(f'{prefix}{col}') and col in df.columns}
    if not active:
        return pd.Series([empty_matches_all] * len(df))
    mask = pd.Series([True] * len(df))
    for col, val in active.items():
        mask &= df[col].astype(str) == val
    return mask


# ── views ─────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def load_metadata(request):
    try:
        data     = json.loads(request.body)
        filepath = data.get('filepath', '').strip()
        if not filepath:
            return JsonResponse({'error': 'No filepath provided'}, status=400)
        if not os.path.exists(filepath):
            return JsonResponse({'error': f'File not found: {filepath}'}, status=404)

        df = pd.read_csv(filepath)

        if 'PATH_TO_KINETIC_DATA' not in df.columns:
            return JsonResponse({'error': 'Missing required column: PATH_TO_KINETIC_DATA'}, status=400)

        # Add flag column if missing
        if FLAG_COLUMN not in df.columns:
            df[FLAG_COLUMN] = ''

        # Add computed columns with empty values if they don't exist yet.
        # Values are expected to already be present in the CSV (pre-computed
        # by the user's pipeline or by generate_sample_data.py).
        for col in COMPUTED_COLS:
            if col not in df.columns:
                df[col] = None

        df = df.reset_index(drop=True)
        df['_row_id'] = df.index

        filter_cols = _derive_filter_cols(df)
        _metadata_cache['df']          = df
        _metadata_cache['filepath']    = filepath
        _metadata_cache['filter_cols'] = filter_cols

        available_filters = {}
        for col in filter_cols:
            available_filters[col] = sorted(df[col].dropna().astype(str).unique().tolist())

        return JsonResponse({
            'success': True,
            'rows':    len(df),
            'columns': df.columns.tolist(),
            'filters': available_filters,
        })
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@require_http_methods(["GET"])
def filter_options(request):
    df = _metadata_cache.get('df')
    if df is None:
        return JsonResponse({'error': 'No metadata loaded'}, status=400)

    prefix   = request.GET.get('prefix', '')
    mask     = _build_mask(df, request.GET, prefix=prefix, empty_matches_all=True)
    filtered = df[mask]

    filter_cols = _metadata_cache.get('filter_cols', [])
    options = {}
    for col in filter_cols:
        if col in df.columns:
            options[col] = sorted(filtered[col].dropna().astype(str).unique().tolist())
    options['matching_count'] = int(mask.sum())

    if prefix == 'primary_':
        flags = [f for f in filtered[FLAG_COLUMN].dropna().astype(str).unique() if f]
        options['current_flag'] = flags[0] if len(flags) == 1 else ('mixed' if flags else '')

    return JsonResponse(options)


def _load_kinetic_rows(rows, is_primary):
    datasets, errors = [], []
    filter_cols = _metadata_cache.get('filter_cols', [])
    for _, row in rows.iterrows():
        kinetic_path = str(row.get('PATH_TO_KINETIC_DATA', ''))
        if not kinetic_path or not os.path.exists(kinetic_path):
            errors.append(f'File not found: {kinetic_path}')
            continue
        try:
            kdf       = pd.read_csv(kinetic_path)
            if len(kdf.columns) < 2:
                errors.append(f'Too few columns: {kinetic_path}')
                continue
            time_col  = kdf.columns[0]
            value_col = kdf.columns[1]
            label_parts = [f"{col}={row[col]}" for col in filter_cols
                           if col in row.index and pd.notna(row[col])]
            label        = ' | '.join(label_parts) or os.path.basename(kinetic_path)
            current_flag = str(row.get(FLAG_COLUMN, '')) if pd.notna(row.get(FLAG_COLUMN, '')) else ''

            def _safe_float(val, default=None):
                try:
                    return float(val) if pd.notna(val) else default
                except Exception:
                    return default

            datasets.append({
                'label':        label,
                'row_id':       int(row['_row_id']),
                'time_col':     time_col,
                'value_col':    value_col,
                'x':            kdf[time_col].tolist(),
                'y':            kdf[value_col].tolist(),
                'is_primary':   is_primary,
                'flag':         current_flag,
                'peak_od_time': _safe_float(row.get('peak_od_time')),
                'auc_start':    _safe_float(row.get('auc_start'), DEFAULT_AUC_START),
                'auc_value':    _safe_float(row.get('auc_value')),
                'gr_start':     _safe_float(row.get('gr_start'), DEFAULT_GR_START),
                'growth_rate':  _safe_float(row.get('growth_rate')),
                'metadata':     {col: str(row[col]) for col in filter_cols
                                 if col in row.index and pd.notna(row[col])},
            })
        except Exception as e:
            errors.append(f'Error reading {kinetic_path}: {str(e)}')
    return datasets, errors


@require_http_methods(["GET"])
def kinetic_data(request):
    df = _metadata_cache.get('df')
    if df is None:
        return JsonResponse({'error': 'No metadata loaded'}, status=400)

    primary_mask      = _build_mask(df, request.GET, prefix='primary_', empty_matches_all=True)
    overlay_mask      = _build_mask(df, request.GET, prefix='overlay_', empty_matches_all=False)
    overlay_only_mask = overlay_mask & ~primary_mask

    primary_rows = df[primary_mask]
    overlay_rows = df[overlay_only_mask]

    primary_datasets, errors        = _load_kinetic_rows(primary_rows, is_primary=True)
    overlay_datasets, overlay_errors = _load_kinetic_rows(overlay_rows, is_primary=False)
    errors += overlay_errors
    datasets = primary_datasets + overlay_datasets

    primary_flags = [f for f in primary_rows[FLAG_COLUMN].dropna().astype(str).unique() if f]
    current_flag  = primary_flags[0] if len(primary_flags) == 1 else ('mixed' if len(primary_flags) > 1 else '')

    return JsonResponse({
        'datasets':      datasets,
        'errors':        errors,
        'primary_count': len(primary_datasets),
        'overlay_count': len(overlay_datasets),
        'current_flag':  current_flag,
        'x_label':       datasets[0]['time_col']  if datasets else 'Time',
        'y_label':       datasets[0]['value_col'] if datasets else 'Value',
    })


@csrf_exempt
@require_http_methods(["POST"])
def save_flag(request):
    df       = _metadata_cache.get('df')
    filepath = _metadata_cache.get('filepath')
    if df is None or filepath is None:
        return JsonResponse({'error': 'No metadata loaded'}, status=400)
    try:
        data            = json.loads(request.body)
        flag_value      = str(data.get('flag', '')).strip()
        primary_filters = data.get('primary_filters', {})

        if flag_value not in FLAG_VALUES:
            return JsonResponse({'error': f'Invalid flag. Must be one of: {FLAG_VALUES}'}, status=400)

        mask = pd.Series([True] * len(df))
        filter_cols = _metadata_cache.get('filter_cols', [])
        for col in filter_cols:
            val = primary_filters.get(col)
            if val and col in df.columns:
                mask &= df[col].astype(str) == val

        if not mask.any():
            return JsonResponse({'error': 'No rows matched the primary filters'}, status=400)

        df.loc[mask, FLAG_COLUMN] = flag_value
        _metadata_cache['df']     = df
        _save_df()

        return JsonResponse({'success': True, 'rows_updated': int(mask.sum()), 'flag': flag_value})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def save_computed(request):
    """
    Update peak_od_time, auc_start, and/or auc_value for a single row.
    If auc_start changes, recompute auc_value from the kinetic file.
    """
    df       = _metadata_cache.get('df')
    filepath = _metadata_cache.get('filepath')
    if df is None or filepath is None:
        return JsonResponse({'error': 'No metadata loaded'}, status=400)
    try:
        data   = json.loads(request.body)
        row_id = int(data['row_id'])

        if row_id not in df.index:
            return JsonResponse({'error': f'Row {row_id} not found'}, status=404)

        updated = {}

        # peak_od_time — user override, just save
        if 'peak_od_time' in data:
            val = float(data['peak_od_time'])
            df.at[row_id, 'peak_od_time'] = round(val, 4)
            updated['peak_od_time'] = round(val, 4)

        # auc_start — save and recompute auc_value
        if 'auc_start' in data:
            new_start = float(data['auc_start'])
            df.at[row_id, 'auc_start'] = round(new_start, 4)
            updated['auc_start'] = round(new_start, 4)
            # Recompute AUC
            kinetic_path = str(df.at[row_id, 'PATH_TO_KINETIC_DATA'])
            if os.path.exists(kinetic_path):
                kdf       = pd.read_csv(kinetic_path)
                xs, ys    = kdf.iloc[:, 0].tolist(), kdf.iloc[:, 1].tolist()
                new_auc   = round(_trapz_auc(xs, ys, new_start), 4)
                df.at[row_id, 'auc_value'] = new_auc
                updated['auc_value'] = new_auc

        # auc_value — user override (no recompute)
        if 'auc_value' in data and 'auc_start' not in data:
            val = float(data['auc_value'])
            df.at[row_id, 'auc_value'] = round(val, 4)
            updated['auc_value'] = round(val, 4)

        # gr_start — save and recompute growth_rate
        if 'gr_start' in data:
            new_start = float(data['gr_start'])
            df.at[row_id, 'gr_start'] = round(new_start, 4)
            updated['gr_start'] = round(new_start, 4)
            kinetic_path = str(df.at[row_id, 'PATH_TO_KINETIC_DATA'])
            if os.path.exists(kinetic_path):
                kdf      = pd.read_csv(kinetic_path)
                xs, ys   = kdf.iloc[:, 0].tolist(), kdf.iloc[:, 1].tolist()
                new_gr   = round(_growth_rate(xs, ys, new_start), 6)
                df.at[row_id, 'growth_rate'] = new_gr
                updated['growth_rate'] = new_gr

        # growth_rate — user override (no recompute)
        if 'growth_rate' in data and 'gr_start' not in data:
            val = float(data['growth_rate'])
            df.at[row_id, 'growth_rate'] = round(val, 6)
            updated['growth_rate'] = round(val, 6)

        _metadata_cache['df'] = df
        _save_df()

        return JsonResponse({'success': True, 'row_id': row_id, 'updated': updated})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
