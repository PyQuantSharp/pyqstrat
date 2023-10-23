# $$_ Lines starting with # $$_* autogenerated by jup_mini. Do not modify these
# $$_code
# $$_ %%checkall
from __future__ import annotations
import numpy as np
import pandas as pd
import types
import sys
from collections import defaultdict
from pprint import pformat
import math
import plotly.graph_objects as go
from pyqstrat.evaluator import compute_return_metrics, display_return_metrics, plot_return_metrics
from pyqstrat.account import Account
from pyqstrat.pq_types import ContractGroup, Contract, Order, Trade, RoundTripTrade, TimeInForce, OrderStatus
from pyqstrat.pq_utils import series_to_array, assert_
from types import SimpleNamespace
from typing import Callable, Any, Union, Sequence
from pyqstrat.pq_utils import get_child_logger


StrategyContextType = SimpleNamespace

PriceFunctionType = Callable[[Contract, np.ndarray, int, StrategyContextType], float]

IndicatorType = Callable[[ContractGroup, np.ndarray, SimpleNamespace, StrategyContextType], np.ndarray]

SignalType = Callable[[ContractGroup, np.ndarray, SimpleNamespace, SimpleNamespace, StrategyContextType], np.ndarray]

RuleType = Callable[
    [ContractGroup, 
     int, 
     np.ndarray, 
     SimpleNamespace, 
     np.ndarray,
     Account, 
     Sequence[Order],
     StrategyContextType],
    list[Order]]

MarketSimulatorType = Callable[
    [Sequence[Order], 
     int, 
     np.ndarray, 
     dict[str, SimpleNamespace], 
     dict[str, SimpleNamespace], 
     SimpleNamespace],
    list[Trade]]

DateRangeType = Union[tuple[str, str], tuple[np.datetime64, np.datetime64]]

# Placeholder for orders to create later
OrderTupType = tuple[RuleType, ContractGroup, dict[str, Any]]

PlotPropertiesType = dict[str, dict[str, Any]]

NAT = np.datetime64('NaT')

_logger = get_child_logger(__name__)


class Strategy:
    def __init__(self, 
                 timestamps: np.ndarray,
                 contract_groups: Sequence[ContractGroup],
                 price_function: PriceFunctionType,
                 starting_equity: float = 1.0e6, 
                 pnl_calc_time: int = 16 * 60 + 1,
                 trade_lag: int = 0,
                 run_final_calc: bool = True, 
                 strategy_context: StrategyContextType | None = None) -> None:
        '''
        Args:
            timestamps (np.array of np.datetime64): The "heartbeat" of the strategy.  We will evaluate trading rules and 
                simulate the market at these times.
            contract_groups: The contract groups we will potentially trade.
            price_function: A function that returns the price of a contract at a given timestamp
            starting_equity: Starting equity in Strategy currency.  Default 1.e6
            pnl_calc_time: Time of day used to calculate PNL.  Default 15 * 60 (3 pm)
            trade_lag: Number of bars you want between the order and the trade.  For example, if you think it will take
                5 seconds to place your order in the market, and your bar size is 1 second, set this to 5.  Set this to 0 if you
                want to execute your trade at the same time as you place the order, for example, if you have daily bars.  Default 0.
            run_final_calc: If set, calculates unrealized pnl and net pnl as well as realized pnl when strategy is done.
                If you don't need unrealized pnl, turn this off for faster run time. Default True
            strategy_context: A storage class where you can store key / value pairs relevant to this strategy.
                For example, you may have a pre-computed table of correlations that you use in the indicator or trade rule functions.  
                If not set, the __init__ function will create an empty member strategy_context object that you can access.
        '''
        self.name = 'main'  # Set by portfolio when running multiple strategies
        increasing_ts: bool = bool(np.all(np.diff(timestamps.astype(int)) > 0))
        # print(increasing_ts, type(increasing_ts))
        assert_(increasing_ts, f'timestamps must be monotonically increasing: {timestamps[:100]} ...')
        self.timestamps = timestamps
        assert_(len(contract_groups) > 0 and isinstance(contract_groups[0], ContractGroup))
        self.contract_groups = contract_groups
        if strategy_context is None: strategy_context = types.SimpleNamespace()
        self.strategy_context = strategy_context
        self.account = Account(contract_groups, timestamps, price_function, strategy_context, starting_equity, pnl_calc_time)
        assert_(trade_lag >= 0, f'trade_lag cannot be negative: {trade_lag}')
        self.trade_lag = trade_lag
        self.run_final_calc = run_final_calc
        self.indicators: dict[str, IndicatorType] = {}
        self.signals: dict[str, SignalType] = {}
        self.signal_values: dict[str, SimpleNamespace] = defaultdict(types.SimpleNamespace)
        self.rule_names: list[str] = []
        self.rules: dict[str, RuleType] = {}
        self.position_filters: dict[str, str | None] = {}
        self.rule_signals: dict[str, tuple[str, Sequence[Any]]] = {}
        self.market_sims: list[MarketSimulatorType] = []
        self._trades: list[Trade] = []
        # a list of all orders created used for display
        self._orders: list[Order] = []
        self._current_orders: list[Order] = []
        self.indicator_deps: dict[str, list[str]] = {}
        self.indicator_cgroups: dict[str, list[ContractGroup]] = {}
        self.indicator_values: dict[str, SimpleNamespace] = defaultdict(types.SimpleNamespace)
        self.signal_indicator_deps: dict[str, list[str]] = {}
        self.signal_deps: dict[str, list[str]] = {}
        self.signal_cgroups: dict[str, list[ContractGroup]] = {}
        self.trades_iter: list[list] = [[] for x in range(len(timestamps))]  # For debugging, we don't really need this as a member variable
        
    def add_indicator(self, 
                      name: str, 
                      indicator: IndicatorType, 
                      contract_groups: Sequence[ContractGroup] | None = None, 
                      depends_on: Sequence[str] | None = None) -> None:
        '''
        Args:
            name: Name of the indicator
            indicator:  A function that takes strategy timestamps and other indicators and returns a numpy array
              containing indicator values.  The return array must have the same length as the timestamps object.
            contract_groups: Contract groups that this indicator applies to.  If not set, it applies to all contract groups. Default None.
            depends_on: Names of other indicators that we need to compute this indicator. Default None.
        '''
        self.indicators[name] = indicator
        self.indicator_deps[name] = [] if depends_on is None else list(depends_on)
        if contract_groups is None: contract_groups = self.contract_groups
        self.indicator_cgroups[name] = list(contract_groups)
        
    def add_signal(self,
                   name: str,
                   signal_function: SignalType,
                   contract_groups: Sequence[ContractGroup] | None = None,
                   depends_on_indicators: Sequence[str] | None = None,
                   depends_on_signals: Sequence[str] | None = None) -> None:
        '''
        Args:
            name (str): Name of the signal
            signal_function (function):  A function that takes timestamps and a dictionary of indicator value arrays and 
                returns a numpy array
                containing signal values.  The return array must have the same length as the input timestamps
            contract_groups (list of :obj:`ContractGroup`, optional): Contract groups that this signal applies to.  
                If not set, it applies to all contract groups.  Default None.
            depends_on_indicators (list of str, optional): Names of indicators that we need to compute this signal. Default None.
            depends_on_signals (list of str, optional): Names of other signals that we need to compute this signal. Default None.
        '''
        self.signals[name] = signal_function
        self.signal_indicator_deps[name] = [] if depends_on_indicators is None else list(depends_on_indicators)
        self.signal_deps[name] = [] if depends_on_signals is None else list(depends_on_signals)
        if contract_groups is None: contract_groups = self.contract_groups
        self.signal_cgroups[name] = list(contract_groups)
        
    def add_rule(self, 
                 name: str, 
                 rule_function: RuleType, 
                 signal_name: str, 
                 sig_true_values: Sequence[Any] | None = None, 
                 position_filter: str | None = None) -> None:
        '''Add a trading rule.  Trading rules are guaranteed to run in the order in which you add them.  For example, if you set trade_lag to 0,
               and want to exit positions and re-enter new ones in the same bar, make sure you add the exit rule before you add the entry rule to the 
               strategy.
        
        Args:
            name: Name of the trading rule
            rule_function: A trading rule function that returns a list of Orders
            signal_name: The strategy will call the trading rule function when the signal with this name matches sig_true_values
            sig_true_values: If the signal value at a bar is equal to one of these values, 
                the Strategy will call the trading rule function.  Default [TRUE]
            position_filter: Can be "zero", "nonzero", "positive", "negative" or None.  
                Rules are only triggered when the corresponding contract positions fit the criteria.
                For example, a positive rule is only triggered when the current position for that contract is > 0
                If not set, we don't look at the position before triggering the rule. Default None
        '''
        
        # import pdb; pdb.set_trace()
        if sig_true_values is None: sig_true_values = [True]
            
        assert_(name not in self.rule_names, f'rule {name} already exists')
        # Rules should be run in order
        self.rule_names.append(name)
        self.rule_signals[name] = (signal_name, sig_true_values)
        self.rules[name] = rule_function
        if position_filter is not None:
            assert_(position_filter in ['zero', 'nonzero', 'positive', 'negative', ''])
        if position_filter == '': position_filter = None
        self.position_filters[name] = position_filter
        
    def add_market_sim(self, market_sim_function: MarketSimulatorType) -> None:
        '''Add a market simulator.  A market simulator is a function that takes orders as input and returns trades.'''
        self.market_sims.append(market_sim_function)
        
    def run_indicators(self, 
                       indicator_names: Sequence[str] | None = None, 
                       contract_groups: Sequence[ContractGroup] | None = None, 
                       clear_all: bool = False) -> None:
        '''Calculate values of the indicators specified and store them.
        
        Args:
            indicator_names: list of indicator names.  If None (default) run all indicators
            contract_groups: Contract group to run this indicator for.  If None (default), we run it for all contract groups.
            clear_all: If set, clears all indicator values before running.  Default False.
        '''
        
        if indicator_names is None: indicator_names = list(self.indicators.keys())
        if contract_groups is None: contract_groups = self.contract_groups
            
        if clear_all: self.indicator_values = defaultdict(types.SimpleNamespace)
            
        ind_names = []
            
        cg_names = set([cg.name for cg in contract_groups])
        for ind_name, cgroup_list in self.indicator_cgroups.items():
            cg_list_names = set([cg.name for cg in cgroup_list])
            if len(cg_names.intersection(cg_list_names)): ind_names.append(ind_name)
                
        indicator_names = list(set(ind_names).intersection(indicator_names))
         
        for cgroup in contract_groups:
            cgroup_ind_namespace = self.indicator_values[cgroup.name]
            for indicator_name in indicator_names:
                # First run all parents
                parent_names = self.indicator_deps[indicator_name]
                for parent_name in parent_names:
                    if cgroup.name in self.indicator_values and hasattr(cgroup_ind_namespace, parent_name): continue
                    self.run_indicators([parent_name], [cgroup])
                    
                # Now run the actual indicator
                if cgroup.name in self.indicator_values and hasattr(cgroup_ind_namespace, indicator_name): continue
                indicator_function = self.indicators[indicator_name]
                     
                parent_values = types.SimpleNamespace()

                for parent_name in parent_names:
                    setattr(parent_values, parent_name, getattr(cgroup_ind_namespace, parent_name))
                    
                indicator_values = indicator_function(cgroup, self.timestamps, parent_values, self.strategy_context)

                setattr(cgroup_ind_namespace, indicator_name, series_to_array(indicator_values))
                
    def run_signals(self, 
                    signal_names: Sequence[str] | None = None, 
                    contract_groups: Sequence[ContractGroup] | None = None, 
                    clear_all: bool = False) -> None:
        '''Calculate values of the signals specified and store them.
        
        Args:
            signal_names: list of signal names.  If None (default) run all signals
            contract_groups: Contract groups to run this signal for. If None (default), we run it for all contract groups.
            clear_all: If set, clears all signal values before running.  Default False.
        '''
        if signal_names is None: signal_names = list(self.signals.keys())
        if contract_groups is None: contract_groups = self.contract_groups
            
        if clear_all: self.signal_values = defaultdict(types.SimpleNamespace)
            
        sig_names = []

        cg_names = set([cg.name for cg in contract_groups])
        for sig_name, cgroup_list in self.signal_cgroups.items():
            cg_list_names = set([cg.name for cg in cgroup_list])
            if len(cg_names.intersection(cg_list_names)): sig_names.append(sig_name)
                
        signal_names = list(set(sig_names).intersection(signal_names))
        
        for cgroup in contract_groups:
            for signal_name in signal_names:
                if cgroup.name not in [cg.name for cg in self.signal_cgroups[signal_name]]: continue
                # First run all parent signals
                parent_names = self.signal_deps[signal_name]
                for parent_name in parent_names:
                    if cgroup.name in self.signal_values and hasattr(self.signal_values[cgroup.name], parent_name): continue
                    self.run_signals([parent_name], [cgroup])
                # Now run the actual signal
                if cgroup.name in self.signal_values and hasattr(self.signal_values[cgroup.name], signal_name): continue
                signal_function = self.signals[signal_name]
                parent_values = types.SimpleNamespace()
                for parent_name in parent_names:
                    sig_vals = getattr(self.signal_values[cgroup.name], parent_name)
                    setattr(parent_values, parent_name, sig_vals)
                    
                # Get indicators needed for this signal
                indicator_values = types.SimpleNamespace()
                for indicator_name in self.signal_indicator_deps[signal_name]:
                    setattr(indicator_values, indicator_name, getattr(self.indicator_values[cgroup.name], indicator_name))
                    
                signal_output = signal_function(cgroup, self.timestamps, indicator_values, parent_values, self.strategy_context)
                setattr(self.signal_values[cgroup.name], signal_name, series_to_array(signal_output))

    def _generate_order_iterations(self, 
                                   rule_names: Sequence[str] | None = None, 
                                   contract_groups: Sequence[ContractGroup] | None = None, 
                                   start_date: np.datetime64 = NAT, 
                                   end_date: np.datetime64 = NAT) -> None:
        '''
        >>> class MockStrat:
        ...    def __init__(self):
        ...        self.timestamps = timestamps
        ...        self.trade_lag = 0
        ...        self.account = self
        ...        self.rules = {'rule_a': rule_a, 'rule_b': rule_b}
        ...        self.market_sims = {'ibm': market_sim_ibm, 'aapl': market_sim_aapl}
        ...        self.rule_signals = {'rule_a': ('sig_a', [1]), 'rule_b': ('sig_b', [1, -1])}
        ...        self.signal_values = {'IBM': types.SimpleNamespace(sig_a=np.array([0., 1., 1.]), 
        ...                                                   sig_b = np.array([0., 0., 0.]) ),
        ...                               'AAPL': types.SimpleNamespace(sig_a=np.array([0., 0., 0.]), 
        ...                                                    sig_b=np.array([0., -1., -1])
        ...                                                   )}
        ...        self.signal_cgroups = {'sig_a': [ibm, aapl], 'sig_b': [ibm, aapl]}
        ...        self.indicator_values = {'IBM': types.SimpleNamespace(), 'AAPL': types.SimpleNamespace()}
        >>>
        >>> def market_sim_aapl(): pass
        >>> def market_sim_ibm(): pass
        >>> def rule_a(): pass
        >>> def rule_b(): pass
        >>> timestamps = np.array(['2018-01-01', '2018-01-02', '2018-01-03'], dtype = 'M8[D]')
        >>> rule_names = ['rule_a', 'rule_b']
        >>> ContractGroup.clear_cache()
        >>> ibm = ContractGroup.get('IBM')
        >>> aapl = ContractGroup.get('AAPL')
        >>> contract_groups = [ibm, aapl]
        >>> start_date = np.datetime64('2018-01-01')
        >>> end_date = np.datetime64('2018-02-05')
        >>> strategy = MockStrat()
        >>> Strategy._generate_order_iterations(strategy, rule_names, contract_groups, start_date, end_date)
        >>> orders_iter = strategy.orders_iter
        >>> assert_(len(orders_iter[0]) == 0)
        >>> assert_(len(orders_iter[1]) == 2)
        >>> assert_(orders_iter[1][0][1] == ibm)
        >>> assert_(orders_iter[1][1][1] == aapl)
        >>> assert_(len(orders_iter[2]) == 2)
        '''
        _start_date, _end_date = np.datetime64(start_date), np.datetime64(end_date)
        if rule_names is None: rule_names = self.rule_names
        if contract_groups is None: contract_groups = self.contract_groups

        num_timestamps = len(self.timestamps)
        
        # list of lists, i -> list of order tuple
        orders_iter: list[list[OrderTupType]] = [[] for x in range(num_timestamps)]
            
        for rule_name in rule_names:
            rule_function = self.rules[rule_name]
            for cgroup in contract_groups:
                signal_name, sig_true_values = self.rule_signals[rule_name]
                if cgroup.name not in [cg.name for cg in self.signal_cgroups[signal_name]]:
                    # We don't need to call this rule for this contract group
                    continue
                assert_(cgroup.name in self.signal_values, f'missing {cgroup.name} in signal_values')
                sig_values = getattr(self.signal_values[cgroup.name], signal_name)
                timestamps = self.timestamps

                null_value = False if sig_values.dtype == np.dtype('bool') else np.nan
                
                if not np.isnat(_start_date):
                    start_idx: int = np.searchsorted(timestamps, _start_date)  # type: ignore
                    sig_values[0:start_idx] = null_value
                    
                if not np.isnat(_end_date):
                    end_idx: int = np.searchsorted(timestamps, _end_date)  # type: ignore
                    sig_values[end_idx:] = null_value

                indices = np.nonzero(np.isin(sig_values[:num_timestamps], sig_true_values))[0]
                
                # Don't run rules on last index since we cannot fill any orders
                if len(indices) and indices[-1] == len(sig_values) - 1 and self.trade_lag > 0: indices = indices[:-1] 
                indicator_values = self.indicator_values[cgroup.name]
                iteration_params = {'indicator_values': indicator_values, 'signal_values': sig_values, 'rule_name': rule_name}
                for idx in indices: orders_iter[idx].append((rule_function, cgroup, iteration_params))

        self.orders_iter = orders_iter
    
    def run_rules(self, 
                  rule_names: Sequence[str] | None = None, 
                  contract_groups: Sequence[ContractGroup] | None = None, 
                  start_date: np.datetime64 = NAT,
                  end_date: np.datetime64 = NAT) -> None:
        '''
        Run trading rules.
        
        Args:
            rule_names: list of rule names.  If None (default) run all rules
            contract_groups: Contract groups to run this rule for.  If None (default), we run it for all contract groups.
            start_date: Run rules starting from this date. Default None 
            end_date: Don't run rules after this date.  Default None
        '''
        self._generate_order_iterations(rule_names, contract_groups, start_date, end_date)
        
        # Now we know which rules, contract groups need to be applied for each iteration, go through each iteration and apply them
        # in the same order they were added to the strategy
        for i in range(len(self.orders_iter)):
            self._run_iteration(i)
            
        if self.run_final_calc:
            self.account.calc(self.timestamps[-1])
        
    def _run_iteration(self, i: int) -> None:
        # first execute open orders so that positions get updated before running rules
        # If the lag is 0, then run rules one by one, and after each rule, run market sim to generate trades and update
        # positions. For example, if we have a rule to exit a position and enter a new one, we should make sure 
        # positions are updated after the first rule before running the second rule.  If the lag is not 0, 
        # run all rules and collect the orders, we don't need to run market sim after each rule
        self._sim_market(i)
        
        rules = self.orders_iter[i]
        
        for j, (rule_function, contract_group, params) in enumerate(rules):
            orders = self._get_orders(i, rule_function, contract_group, params)
            self._orders += orders
            self._current_orders += orders
            # _logger.info(f'current_orders: {self._current_orders}')
            
            if self.trade_lag == 0:
                # we don't need to do this for the last rule function 
                # since the sim_market at the beginning of this function will take care of it
                # in the next iteration
                self._sim_market(i)
            else:
                self._update_current_orders()
                
    def _update_current_orders(self) -> None:
        '''
        Remove any orders that are not open
        '''
        # _logger.info(f'before update: {self._current_orders}')
        if any([not order.is_open() for order in self._current_orders]):
            self._current_orders = [order for order in self._current_orders if order.is_open()]
        # _logger.info(f'after update: {self._current_orders}')
            
    def run(self) -> None:
        self.run_indicators()
        self.run_signals()
        self.run_rules()
        
    def _get_orders(self, idx: int, rule_function: RuleType, contract_group: ContractGroup, params: dict[str, Any]) -> list[Order]:
        try:
            indicator_values, signal_values, rule_name = params['indicator_values'], params['signal_values'], params['rule_name']
            position_filter = self.position_filters[rule_name]

            if position_filter is not None:
                curr_pos = self.account.position(contract_group, self.timestamps[idx])
                if position_filter == 'zero' and not math.isclose(curr_pos, 0): return []
                if position_filter == 'nonzero' and math.isclose(curr_pos, 0): return []
                if position_filter == 'positive' and (curr_pos < 0 or math.isclose(curr_pos, 0)): return []
                if position_filter == 'negative' and (curr_pos > 0 or math.isclose(curr_pos, 0)): return []
                
            orders = rule_function(contract_group, idx, self.timestamps, indicator_values, signal_values, self.account,
                                   self._current_orders, self.strategy_context)
        except Exception as e:
            raise type(e)(
                f'Exception: {str(e)} at rule: {type(rule_function)} contract_group: {contract_group} index: {idx}'
            ).with_traceback(sys.exc_info()[2])
        return orders
            
    def _sim_market(self, i: int) -> None:
        '''
        Go through all open orders and run market simulators to generate a list of trades and return any orders that were not filled.
        '''
        for order in self._current_orders:
            idx = np.searchsorted(self.timestamps, order.timestamp)
            assert_(bool(idx >= 0 and idx < len(self.timestamps) and idx <= i), 
                    f'{i} {idx} {len(self.timestamps)} {order.timestamp}')
            # _logger.info(f'{idx} {i} {self.trade_lag}')
            
            if (i - idx) < self.trade_lag:
                continue
            if (i - idx) > self.trade_lag:
                if order.time_in_force == TimeInForce.FOK:
                    # _logger.info('cancelling a')
                    order.cancel()
                    continue
            # i - idx == self.trade_lag
            if order.status == OrderStatus.CANCEL_REQUESTED:
                # _logger.info('cancelling')
                order.cancel()
                
            if order.time_in_force == TimeInForce.DAY:
                if self.timestamps[i].astype('M8[D]') > order.timestamp.astype('M8[D]'):
                    order.cancel()
                
        for market_sim_function in self.market_sims:
            try:
                # import pdb; pdb.set_trace();
                self._update_current_orders()
                
                trades = market_sim_function(self._current_orders, 
                                             i, 
                                             self.timestamps, 
                                             self.indicator_values, 
                                             self.signal_values, 
                                             self.strategy_context)
                if len(trades): self.account.add_trades(trades)
                self._trades += trades
            except Exception as e:
                raise type(e)(f'Exception: {str(e)} at index: {i} function: {market_sim_function}').with_traceback(sys.exc_info()[2])
                
        self._update_current_orders()
            
    def df_data(self, 
                contract_groups: Sequence[ContractGroup] | None = None, 
                add_pnl: bool = True, 
                start_date: str | np.datetime64 = NAT, 
                end_date: str | np.datetime64 = NAT) -> pd.DataFrame:
        '''
        Add indicators and signals to end of market data and return as a pandas dataframe.
        
        Args:
            contract_groups (list of:obj:`ContractGroup`, optional): list of contract groups to include.  All if set to None (default)
            add_pnl: If True (default), include P&L columns in dataframe
            start_date: string or numpy datetime64. Default None
            end_date: string or numpy datetime64: Default None
        '''
        _start_date, _end_date = np.datetime64(start_date), np.datetime64(end_date)
        if contract_groups is None: contract_groups = self.contract_groups
            
        timestamps = self.timestamps
        
        if not np.isnat(_start_date): timestamps = timestamps[timestamps >= _start_date]
        if not np.isnat(_end_date): timestamps = timestamps[timestamps <= _end_date]
            
        dfs = []
             
        for contract_group in contract_groups:
            df = pd.DataFrame({'timestamp': self.timestamps})
            if add_pnl: 
                df_pnl = self.df_pnl(contract_group)
                
            indicator_values = self.indicator_values[contract_group.name]
            
            for k in sorted(indicator_values.__dict__):
                name = k
                # Avoid name collisions
                if name in df.columns: name = name + '.ind'
                df.insert(len(df.columns), name, getattr(indicator_values, k))

            signal_values = self.signal_values[contract_group.name]

            for k in sorted(signal_values.__dict__):
                name = k
                if name in df.columns: name = name + '.sig'
                df.insert(len(df.columns), name, getattr(signal_values, k))
                
            if add_pnl: df = pd.merge(df, df_pnl, on=['timestamp'], how='left')
            # Add counter column for debugging
            df.insert(len(df.columns), 'i', np.arange(len(df)))
            
            dfs.append(df)
            
        return pd.concat(dfs)
    
    def trades(self, 
               contract_group: ContractGroup | None = None, 
               start_date: np.datetime64 = NAT, 
               end_date: np.datetime64 = NAT) -> list[Trade]:
        '''Returns a list of trades with the given contract group and with trade date between (and including) start date 
            and end date if they are specified.
            If contract_group is None trades for all contract_groups are returned'''
        return self.account.trades(contract_group, start_date, end_date)
    
    def roundtrip_trades(self,
                         contract_group: ContractGroup | None = None, 
                         start_date: np.datetime64 = NAT, 
                         end_date: np.datetime64 = NAT) -> list[RoundTripTrade]:
        '''Returns a list of trades with the given contract group and with trade date between (and including) start date 
            and end date if they are specified.
            If contract_group is None trades for all contract_groups are returned'''
        return self.account.roundtrip_trades(contract_group, start_date, end_date)
    
    def df_trades(self, 
                  contract_group: ContractGroup | None = None, 
                  start_date: np.datetime64 = NAT, 
                  end_date: np.datetime64 = NAT) -> pd.DataFrame:
        '''Returns a dataframe with data from trades with the given contract group and with trade date between (and including)
            start date and end date
            if they are specified. If contract_group is None trades for all contract_groups are returned'''
        return self.account.df_trades(contract_group, start_date, end_date)
    
    def df_roundtrip_trades(self, 
                            contract_group: ContractGroup | None = None, 
                            start_date: np.datetime64 = NAT, 
                            end_date: np.datetime64 = NAT) -> pd.DataFrame:
        '''Returns a dataframe of round trip trades with the given contract group and with trade date 
            between (and including) start date and end date if they are specified. If contract_group is None trades for all 
            contract_groups are returned'''
        return self.account.df_roundtrip_trades(contract_group, start_date, end_date)

    def orders(self, 
               contract_group: ContractGroup | None = None, 
               start_date: np.datetime64 | str | None = None, 
               end_date: np.datetime64 | str | None = None) -> list[Order]:
        '''Returns a list of orders with the given contract group and with order date between (and including) start date and 
            end date if they are specified.
            If contract_group is None orders for all contract_groups are returned'''
        orders: list[Order] = []
        _start_date: np.datetime64 = np.datetime64(start_date)
        _end_date: np.datetime64 = np.datetime64(end_date)
        if contract_group is None:
            orders += [order for order in self._orders if (
                np.isnat(_start_date) or np.datetime64(order.timestamp) >= _start_date) and (
                np.isnat(_end_date) or np.datetime64(order.timestamp) <= _end_date)]
        else:
            for contract in contract_group.contracts:
                orders += [order for order in self._orders if (contract is None or order.contract == contract) and (
                    np.isnat(_start_date) or np.datetime64(order.timestamp) >= _start_date) and (
                    np.isnat(_end_date) or np.datetime64(order.timestamp) <= _end_date)]
        return orders
    
    def df_orders(self, 
                  contract_group: ContractGroup | None = None, 
                  start_date: np.datetime64 | str = NAT, 
                  end_date: np.datetime64 | str = NAT) -> pd.DataFrame:
        '''Returns a dataframe with data from orders with the given contract group and with order date between (and including) 
            start date and end date
            if they are specified. If contract_group is None orders for all contract_groups are returned'''
        orders = self.orders(contract_group, start_date, end_date)
        order_records = [(order.contract.symbol if order.contract else '',
                          type(order).__name__, order.timestamp, order.qty, 
                          order.reason_code, 
                          (str(order.properties.__dict__) if order.properties.__dict__ else ''),
                          (str(order.contract.properties.__dict__) 
                           if order.contract and order.contract.properties.__dict__ else '')) for order in orders]
        df_orders = pd.DataFrame.from_records(order_records,
                                              columns=['symbol', 'type', 'timestamp', 'qty', 'reason_code', 'order_props', 'contract_props'])
        return df_orders
    
    def df_pnl(self, contract_group=None) -> pd.DataFrame:
        '''Returns a dataframe with P&L columns.  If contract group is set to None (default), sums up P&L across all contract groups'''
        return self.account.df_account_pnl(contract_group)
    
    def df_returns(self, 
                   contract_group: ContractGroup | None = None,
                   sampling_frequency: str = 'D') -> pd.DataFrame:
        '''Return a dataframe of returns and equity indexed by date.
        
        Args:
            contract_group: The contract group to get returns for.  
                If set to None (default), we return the sum of PNL for all contract groups
            sampling_frequency: Downsampling frequency.  Default is None.  See pandas frequency strings for possible values
        '''
        pnl = self.df_pnl(contract_group)[['timestamp', 'net_pnl', 'equity']]
        _logger.info(pnl)
        pnl.equity = pnl.equity.ffill()
        pnl = pnl.set_index('timestamp').resample(sampling_frequency).last().reset_index()
        pnl = pnl.dropna(subset=['equity'])
        ret = pnl.equity.pct_change().values
        ret[0] = pnl.equity.values[0] / self.account.starting_equity - 1
        pnl['ret'] = ret
        return pnl
                
    def evaluate_returns(self, 
                         contract_group: ContractGroup | None = None, 
                         periods_per_year: int = 0,
                         plot: bool = True, 
                         display_summary: bool = True, 
                         float_precision: int = 4, 
                         return_metrics: bool = True) -> dict[str, Any] | None:
        '''Computes return metrics and does or more of plotting, displaying or returning them.
        
        Args:
            contract_group (:obj:`ContractGroup`, optional): Contract group to evaluate or None (default) for all contract groups
            periods_per_year (int): If set to 0, we try to infer the frequency from the timestamps in the returns
                sometimes this is not possible, for example if you have daily returns with random gaps in the days
                In that case, you should set this value yourself. Use 252 if returns are on a daily frequency
            plot (bool): If set to True, display plots of equity, drawdowns and returns.  Default False
            float_precision (float, optional): Number of significant figures to show in returns.  Default 4
            return_metrics (bool, optional): If set, we return the computed metrics as a dictionary
        '''
        returns = self.df_returns(contract_group)
        ev = compute_return_metrics(returns.timestamp.values, returns.ret.values, 
                                    self.account.starting_equity, periods_per_year=periods_per_year)
        if display_summary:
            display_return_metrics(ev.metrics(), float_precision=float_precision)
        if plot: 
            plot_return_metrics(ev.metrics())
        if return_metrics:
            return ev.metrics()
        return None
    
    def plot_returns(self, contract_group: ContractGroup | None = None) -> go.Figure:
        '''Display plots of equity, drawdowns and returns for the given contract group or for all contract groups if contract_group 
            is None (default)'''
        if contract_group is None:
            returns = self.df_returns()
        else:
            returns = self.df_returns(contract_group)

        ev = compute_return_metrics(returns.timestamp.values, returns.ret.values, self.account.starting_equity)
        return plot_return_metrics(ev.metrics())
       
    def __repr__(self):
        return f'{pformat(self.indicators)} {pformat(self.rules)} {pformat(self.account)}'
    

if __name__ == "__main__":
    from test_strategy import test_strategy, test_strategy_2
    test_strategy()
    test_strategy_2()
    import doctest
    doctest.testmod(optionflags=doctest.NORMALIZE_WHITESPACE)
# $$_end_code
