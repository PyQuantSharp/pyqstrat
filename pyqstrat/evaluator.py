#cell 0
import pandas as pd
import numpy as np
from pybt.pybt_utils import *
from pybt.plot import *

#cell 1
_VERBOSE = False

def compute_amean(returns):
    if not len(returns): return np.nan
    return np.nanmean(returns)

def compute_periods_per_year(dates):
    freq = infer_frequency(dates)
    return 252. * freq

def compute_gmean(returns, periods_per_year):
    assert(periods_per_year > 0)
    if not len(returns): return np.nan
    g_mean = ((1.0 + returns).prod())**(1.0/len(returns))
    g_mean = np.power(g_mean, periods_per_year) - 1.0
    return g_mean

def compute_std(returns):
    if not len(returns): return np.nan
    return np.nanstd(returns)

def compute_sortino(returns, amean, periods_per_year):
    if not len(returns) or not np.isfinite(amean) or periods_per_year <= 0: return np.nan
    returns = np.where((~np.isfinite(returns)), 0.0, returns)
    normalized_rets = np.where(returns > 0.0, 0.0, returns)
    sortino_denom = np.std(normalized_rets)
    sortino = np.nan if sortino_denom == 0 else amean / sortino_denom * np.sqrt(periods_per_year)
    return sortino

def compute_sharpe0(returns, amean, periods_per_year):
    if not len(returns) or not np.isfinite(amean) or periods_per_year <= 0: return np.nan
    returns = np.where((~np.isfinite(returns)), 0.0, returns)
    s = np.std(returns)
    sharpe = np.nan if s == 0 else amean / s * np.sqrt(periods_per_year)
    return sharpe

def compute_equity(dates, starting_equity, returns):
    return starting_equity * np.cumprod(1. + returns)

def compute_rolling_dd(dates, equity):
    assert(len(dates) == len(equity))
    if not len(dates): return np.array([], dtype = 'M8[ns]'), np.array([], dtype = np.float)
    s = pd.Series(equity, index = dates)
    rolling_max = s.expanding(min_periods = 1).max()
    dd = np.where(s >= rolling_max, 0.0, (s - rolling_max) / rolling_max)
    return dates, dd

def compute_maxdd_pct(rolling_dd):
    if not len(rolling_dd): return np.nan
    return np.min(rolling_dd)

def compute_maxdd_date(rolling_dd_dates, rolling_dd):
    if not len(rolling_dd_dates): return pd.NaT
    assert(len(rolling_dd_dates) == len(rolling_dd))
    return rolling_dd_dates[np.argmin(rolling_dd)]

def compute_maxdd_start(rolling_dd_dates, rolling_dd, mdd_date):
    if not len(rolling_dd_dates) or pd.isnull(mdd_date): return pd.NaT
    assert(len(rolling_dd_dates) == len(rolling_dd))
    return rolling_dd_dates[(rolling_dd >= 0) & (rolling_dd_dates < mdd_date)][-1]

def compute_mar(returns, periods_per_year, mdd_pct):
    if not len(returns) or np.isnan(mdd_pct): return np.nan
    return np.mean(returns) * periods_per_year / mdd_pct

def compute_dates_3yr(dates):
    if not len(dates): return np.array([], dtype = 'M8[D]')
    last_date = dates[-1]
    d = pd.to_datetime(last_date)
    start_3yr = np.datetime64( d.replace(year = d.year - 3))
    return dates[dates > start_3yr]

def compute_returns_3yr(dates, returns):
    if not len(dates): return np.array([], dtype = np.float)
    assert(len(dates) == len(returns))
    dates_3yr = compute_dates_3yr(dates)
    return returns[dates >= dates_3yr[0]]

def compute_rolling_dd_3yr(dates, equity):
    if not len(dates): return np.array([], dtype = 'M8[D]')
    last_date = dates[-1]
    d = pd.to_datetime(last_date)
    start_3yr = np.datetime64( d.replace(year = d.year - 3))
    equity = equity[dates >= start_3yr]
    dates = dates[dates >= start_3yr]
    return compute_rolling_dd(dates, equity)

def compute_maxdd_pct_3yr(rolling_dd_3yr):
    return compute_maxdd_pct(rolling_dd_3yr)

def compute_maxdd_date_3yr(rolling_dd_3yr_dates, rolling_dd_3yr):
    return compute_maxdd_date(rolling_dd_3yr_dates, rolling_dd_3yr)

def compute_maxdd_start_3yr(rolling_dd_3yr_dates, rolling_dd_3yr, mdd_date_3yr):
    return compute_maxdd_start(rolling_dd_3yr_dates, rolling_dd_3yr, mdd_date_3yr)

def compute_calmar(returns_3yr, periods_per_year, mdd_pct_3yr):
    return compute_mar(returns_3yr, periods_per_year, mdd_pct_3yr)

def compute_bucketed_returns(dates, returns):
    assert(len(dates) == len(returns))
    if not len(dates): return np.array([], dtype = np.str), np.array([], dtype = np.float)
    s = pd.Series(returns, index = dates)
    years_list = []
    rets_list = []
    for year, rets in s.groupby(s.index.map(lambda x : x.year)):
        years_list.append(year)
        rets_list.append(rets.values)
    
    return years_list, rets_list

def compute_annual_returns(dates, returns, periods_per_year):
    assert(len(dates) == len(returns) and periods_per_year > 0)
    if not len(dates): return np.array([], dtype = np.str), np.array([], dtype = np.float)
    s = pd.Series(returns, index = dates)
    ret_by_year = s.groupby(s.index.map(lambda x: x.year)).agg(
        lambda y: compute_gmean(y, periods_per_year))
    return ret_by_year.index.values, ret_by_year.values

class Evaluator:
    def __init__(self, initial_metrics):
        self.metric_values = initial_metrics
        self._metrics = {}
        
    def add_scalar_metric(self, name, func, dependencies):
        self._metrics[name] = ('scalar', func, dependencies)
    
    def add_rolling_metric(self, name, func, dependencies):
        self._metrics[name] = ('rolling', func, dependencies)
        
    def add_bucketed_metric(self, name, func, dependencies):
        self._metrics[name] = ('bucketed', func, dependencies)
        
    def compute(self, metric_names = None):
        if metric_names is None: metric_names = list(self._metrics.keys())
        for metric_name in metric_names:
            if _VERBOSE: print(f'computing: {metric_name}')
            self.compute_metric(metric_name)
            
    def compute_metric(self, metric_name):
        metric_type, func, dependencies = self._metrics[metric_name]
        for dependency in dependencies:
            if dependency not in self.metric_values:
                self.compute_metric(dependency)
        dependency_values = {k: self.metric_values[k] for k in dependencies}
        
        if metric_type == 'scalar':
            values = func(**dependency_values)
        elif metric_type == 'rolling':
            dates, values = func(**dependency_values)
            assert(len(dates) == len(values))
            self.metric_values[metric_name + '_dates'] = dates
        elif metric_type == 'bucketed':
            bucket_names, values = func(**dependency_values)
            assert(len(bucket_names) == len(values))
            self.metric_values[metric_name + '_buckets'] = bucket_names
        else:
            raise Exception(f'unknown metric type: {metric_type}')
            
        self.metric_values[metric_name] = values
                
    def values(self, metric_name):
        return self.metric_values[metric_name]
    
    def metrics(self):
        return self.metric_values
    
    
def compute_return_metrics(dates, rets, starting_equity):
    assert(starting_equity > 0.)
    assert(type(rets) == np.ndarray and rets.dtype == np.float64)
    assert(type(dates) == np.ndarray and np.issubdtype(dates.dtype, np.datetime64) and monotonically_increasing(dates))

    rets = nan_to_zero(rets)

    ev = Evaluator({'dates' : dates, 'returns' : rets, 'starting_equity' : starting_equity})
    ev.add_scalar_metric('periods_per_year', compute_periods_per_year, dependencies = ['dates'])
    ev.add_scalar_metric('amean', compute_amean, dependencies = ['returns'])
    ev.add_scalar_metric('std', compute_std, dependencies = ['returns'])
    ev.add_scalar_metric('up_periods', lambda returns : len(returns[returns > 0]), dependencies = ['returns'])
    ev.add_scalar_metric('down_periods', lambda returns : len(returns[returns < 0]), dependencies = ['returns'])
    ev.add_scalar_metric('up_pct', lambda up_periods, down_periods : up_periods * 1.0 / (up_periods + down_periods), dependencies=['up_periods', 'down_periods'])
    ev.add_scalar_metric('gmean', compute_gmean, dependencies=['returns', 'periods_per_year'])
    ev.add_scalar_metric('sharpe0', compute_sharpe0, dependencies = ['returns', 'periods_per_year', 'amean'])
    ev.add_scalar_metric('sortino', compute_sortino, dependencies = ['returns', 'periods_per_year', 'amean'])
    ev.add_scalar_metric('equity', compute_equity, dependencies = ['dates', 'starting_equity', 'returns'])
    
    # Drawdowns
    ev.add_rolling_metric('rolling_dd', compute_rolling_dd, dependencies = ['dates', 'equity'])
    ev.add_scalar_metric('mdd_pct', compute_maxdd_pct, dependencies = ['rolling_dd'])
    ev.add_scalar_metric('mdd_date', compute_maxdd_date, dependencies = ['rolling_dd_dates', 'rolling_dd'])
    ev.add_scalar_metric('mdd_start', compute_maxdd_start, dependencies = ['rolling_dd_dates', 'rolling_dd', 'mdd_date'])
    ev.add_scalar_metric('mar', compute_mar, dependencies = ['returns', 'periods_per_year', 'mdd_pct'])
    
    ev.add_scalar_metric('dates_3yr', compute_dates_3yr, dependencies = ['dates'])
    ev.add_scalar_metric('returns_3yr', compute_returns_3yr, dependencies = ['dates', 'returns'])

    ev.add_rolling_metric('rolling_dd_3yr', compute_rolling_dd_3yr, dependencies = ['dates', 'equity'])
    ev.add_scalar_metric('mdd_pct_3yr', compute_maxdd_pct_3yr, dependencies = ['rolling_dd_3yr'])
    ev.add_scalar_metric('mdd_date_3yr', compute_maxdd_date_3yr, dependencies = ['rolling_dd_3yr_dates', 'rolling_dd_3yr'])
    ev.add_scalar_metric('mdd_start_3yr', compute_maxdd_start_3yr, dependencies = ['rolling_dd_3yr_dates', 'rolling_dd_3yr', 'mdd_date_3yr'])
    ev.add_scalar_metric('calmar', compute_calmar, dependencies = ['returns_3yr', 'periods_per_year', 'mdd_pct_3yr'])

    ev.add_bucketed_metric('annual_returns', compute_annual_returns, dependencies=['dates', 'returns', 'periods_per_year'])
    ev.add_bucketed_metric('bucketed_returns', compute_bucketed_returns, dependencies=['dates', 'returns'])

    ev.compute()
    return ev

def display_return_metrics(metrics, float_precision = 3):
    from IPython.core.display import display
    
    _metrics = {}
    cols = ['gmean', 'amean', 'std', 'shrp', 'srt', 'calmar', 'mar', 'mdd_pct', 'mdd_start', 'mdd_date', 'dd_3y_pct', 'up_periods', 'down_periods', 'up_pct',
           'mdd_start_3yr', 'mdd_date_3yr']
    
    translate = {'shrp' : 'sharpe0', 'srt' : 'sortino', 'dd_3y_pct' : 'mdd_pct_3yr'}
    for col in cols:
        key = col
        if col in translate: key = translate[col]
        _metrics[col] = metrics[key]
            
    _metrics['mdd_dates'] = f'{str(metrics["mdd_start"])[:10]}/{str(metrics["mdd_date"])[:10]}'
    _metrics['up_dwn'] = f'{metrics["up_periods"]}/{metrics["down_periods"]}/{metrics["up_pct"]:.3g}'
    _metrics['dd_3y_dates'] = f'{str(metrics["mdd_start_3yr"])[:10]}/{str(metrics["mdd_date_3yr"])[:10]}'
    
    years = metrics['annual_returns_buckets']
    ann_rets = metrics['annual_returns']
    for i, year in enumerate(years):
        _metrics[str(year)] = ann_rets[i]
        
    format_str = '{:.' + str(float_precision) + 'g}'
        
    for k, v in _metrics.items():
        if isinstance(v, np.float) or isinstance(v, float):
            _metrics[k] = format_str.format(v)
       
    cols = ['gmean', 'amean', 'std', 'shrp', 'srt', 'calmar', 'mar', 'mdd_pct', 'mdd_dates', 'dd_3y_pct', 'dd_3y_dates', 'up_dwn'
           ] + [str(year) for year in sorted(years)]
    
    df = pd.DataFrame(index = [''])
    for metric_name, metric_value in _metrics.items():
        df.insert(0, metric_name, metric_value)
    df = df[cols]
        
    display(df)
    return df

def plot_return_metrics(metrics, title = None):
    returns = metrics['returns']
    dates = metrics['dates']
    equity =  metrics['equity']
    equity = TimeSeries('equity', dates = dates, values = equity)
    mdd_date, mdd_start = metrics['mdd_start'], metrics['mdd_date']
    mdd_date_3yr, mdd_start_3yr = metrics['mdd_start_3yr'], metrics['mdd_date_3yr']
    drawdown_lines = [DateLine(name = 'max dd', date = mdd_start, color = 'red'),
                      DateLine(date = mdd_date, color = 'red'),
                      DateLine(name = '3y dd', date = mdd_start_3yr, color = 'orange'),
                      DateLine(date = mdd_date_3yr, color = 'orange')]
    equity_subplot = Subplot(equity, title = 'Equity', height_ratio = 0.6, log_y = True, y_tick_format = '${x:,.0f}', 
                             date_lines = drawdown_lines, horizontal_lines=[HorizontalLine(metrics['starting_equity'], color = 'black')]) 
    

    rolling_dd = TimeSeries('drawdowns', dates = metrics['rolling_dd_dates'], values = metrics['rolling_dd'])
    zero_line = HorizontalLine(y = 0, color = 'black')
    dd_subplot = Subplot(rolling_dd, title = 'Drawdowns', height_ratio = 0.2, date_lines = drawdown_lines, horizontal_lines = [zero_line])
    
    years = metrics['bucketed_returns_buckets']
    ann_rets = metrics['bucketed_returns']
    ann_ret = BucketedValues('annual returns', bucket_names = years, bucket_values = ann_rets)
    ann_ret_subplot = Subplot(ann_ret, 'Annual Returns', height_ratio = 0.2, horizontal_lines=[zero_line])
    
    plt = Plot([equity_subplot, dd_subplot, ann_ret_subplot], title = title)
    plt.draw()
    
if __name__ == "__main__":
    from datetime import datetime, timedelta
    np.random.seed(10)
    dates = np.arange(datetime(2018, 1, 1), datetime(2018, 3, 1), timedelta(days = 1))
    rets = np.random.normal(size = len(dates)) / 1000
    starting_equity = 1.e6
    
    ev = compute_return_metrics(dates, rets, starting_equity)
    metrics = display_return_metrics(ev.metrics());
    plot_return_metrics(ev.metrics())
   
    #print(ev.values('sharpe0'))
    #print(ev.values('sortino'))
    #print(ev.values('rolling_dd'))
    #print(f'annual_returns: {ev.values("annual_returns_buckets")} {ev.values("annual_returns")}')
    #print(f'{ev.values("mdd_start")} {ev.values("mdd_date")}')

#cell 2

