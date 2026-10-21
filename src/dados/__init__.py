"""Coleta de dados: Fundamentus (fundamentals B3) + yfinance (preço/dividendos)."""

from .cache import Cache
from .fundamentus import FundamentusClient, ErroFundamentus
from .yahoo import YahooClient

__all__ = ["Cache", "FundamentusClient", "ErroFundamentus", "YahooClient"]
