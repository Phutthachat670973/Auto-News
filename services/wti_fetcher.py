# -*- coding: utf-8 -*-
"""
WTI Futures Fetcher
ดึงข้อมูลราคา WTI Futures จาก Yahoo Finance + EIA
"""

import time
import requests
from datetime import datetime, timedelta
from typing import Tuple, List, Dict
from config.settings import TZ

class WTIFuturesFetcher:
    """ดึงข้อมูลราคา WTI Futures"""
    
    def __init__(self, api_key: str = None):
        self.eia_api_key = api_key
        self.eia_base_url = "https://api.eia.gov/v2"
    
    def fetch_futures_from_yahoo(self) -> Tuple[List[Dict], float]:
        """ดึงข้อมูล WTI Futures จาก Yahoo Finance"""
        try:
            print("[WTI/Yahoo] กำลังดึงข้อมูล Futures จาก Yahoo Finance...")
            
            contracts = {
                'CL=F': 'Front Month',
                'CLG26.NYM': 'Feb 2026',
                'CLH26.NYM': 'Mar 2026',
                'CLJ26.NYM': 'Apr 2026',
                'CLK26.NYM': 'May 2026',
                'CLM26.NYM': 'Jun 2026',
                'CLN26.NYM': 'Jul 2026',
                'CLQ26.NYM': 'Aug 2026',
                'CLU26.NYM': 'Sep 2026',
                'CLV26.NYM': 'Oct 2026',
                'CLX26.NYM': 'Nov 2026',
                'CLZ26.NYM': 'Dec 2026',
                'CLF27.NYM': 'Jan 2027'
            }
            
            futures_data = []
            base_price = None
            
            for symbol, month_label in contracts.items():
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    params = {'interval': '1d', 'range': '5d'}
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
                    
                    response = requests.get(url, params=params, headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        data = response.json()
                        
                        if 'chart' in data and 'result' in data['chart'] and data['chart']['result']:
                            result = data['chart']['result'][0]
                            
                            if 'meta' in result and 'regularMarketPrice' in result['meta']:
                                price = result['meta']['regularMarketPrice']
                                
                                if symbol == 'CL=F':
                                    base_price = price
                                    print(f"[WTI/Yahoo] ✓ Current Price: ${price:.2f}/barrel")
                                else:
                                    if base_price:
                                        change = price - base_price
                                        change_pct = (change / base_price) * 100
                                    else:
                                        prev_close = result['meta'].get('chartPreviousClose', price)
                                        change = price - prev_close
                                        change_pct = (change / prev_close) * 100 if prev_close else 0
                                    
                                    futures_data.append({
                                        "month": month_label,
                                        "contract": symbol.replace('.NYM', ''),
                                        "price": round(price, 2),
                                        "change": round(change, 2),
                                        "change_pct": round(change_pct, 2)
                                    })
                    
                    time.sleep(0.2)
                    
                except Exception as e:
                    print(f"[WTI/Yahoo] Warning for {symbol}: {str(e)}")
                    continue
            
            if futures_data and base_price:
                print(f"[WTI/Yahoo] ✓ ดึงข้อมูล {len(futures_data)} สัญญา")
                return futures_data, base_price
            
            return [], None
                
        except Exception as e:
            print(f"[WTI/Yahoo] Error: {str(e)}")
            return [], None
    
    def fetch_current_wti_price(self) -> Tuple[float, str]:
        """ดึงราคา WTI Spot Price จาก EIA"""
        if not self.eia_api_key:
            return None, None
            
        url = f"{self.eia_base_url}/petroleum/pri/spt/data/"
        params = {
            "api_key": self.eia_api_key,
            "frequency": "daily",
            "data[0]": "value",
            "facets[product][]": "EPCWTI",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "length": 1
        }
        
        try:
            print(f"[WTI/EIA] กำลังดึงราคา WTI Spot Price (Fallback)...")
            response = requests.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            
            response_data = data['response']['data']
            if response_data:
                price = float(response_data[0]['value'])
                period = response_data[0].get('period', '')
                print(f"[WTI/EIA] ✓ Spot Price: ${price:.2f}/barrel ({period})")
                return price, period
                
        except Exception as e:
            print(f"[WTI/EIA] Warning: {str(e)}")
        
        return None, None
    
    def _estimate_futures_from_spot(self, spot_price: float) -> List[Dict]:
        """คำนวณ futures จาก spot price"""
        futures_data = []
        now = datetime.now(TZ)
        monthly_premium = 0.35
        
        for i in range(12):
            months_ahead = i + 1
            future_date = now + timedelta(days=30 * months_ahead)
            premium = months_ahead * monthly_premium
            future_price = spot_price + premium
            
            futures_data.append({
                "month": future_date.strftime("%b %Y"),
                "contract": future_date.strftime("%b%y").upper(),
                "price": round(future_price, 2),
                "change": round(premium, 2),
                "change_pct": round((premium / spot_price) * 100, 2)
            })
        
        return futures_data
    
    def get_current_and_futures(self) -> Dict:
        """ดึงข้อมูลราคาปัจจุบันและ futures"""
        print("\n[WTI] กำลังดึงข้อมูลราคา WTI Futures...")
        
        # Strategy 1: Yahoo Finance
        futures_data, current_price = self.fetch_futures_from_yahoo()
        
        if futures_data and current_price:
            print(f"[WTI] ✓ ใช้ข้อมูลจาก Yahoo Finance - {len(futures_data)} สัญญา")
            
            return {
                "current": {
                    "source": "Yahoo Finance (NYMEX)",
                    "current_price": current_price,
                    "timestamp": datetime.now(TZ).isoformat(),
                    "currency": "USD/barrel",
                    "commodity": "WTI Crude Oil Futures"
                },
                "futures": futures_data[:12],
                "updated_at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
                "is_estimated": False,
                "method": "Real-time data from Yahoo Finance (NYMEX)"
            }
        
        # Strategy 2: EIA Spot + Estimation
        print("[WTI] Yahoo Finance ไม่สำเร็จ กำลังใช้ EIA Spot Price...")
        spot_price, spot_date = self.fetch_current_wti_price()
        
        if spot_price:
            print(f"[WTI] ✓ ใช้ EIA Spot Price + คำนวณ Futures")
            futures_data = self._estimate_futures_from_spot(spot_price)
            
            return {
                "current": {
                    "source": f"U.S. EIA Spot Price ({spot_date})",
                    "current_price": spot_price,
                    "timestamp": datetime.now(TZ).isoformat(),
                    "currency": "USD/barrel",
                    "commodity": "WTI Crude Oil (Cushing, OK)"
                },
                "futures": futures_data,
                "updated_at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
                "is_estimated": True,
                "method": "EIA spot price + statistical estimation"
            }
        
        # Strategy 3: Default fallback
        print("[WTI] ⚠️ ทุกแหล่งล้มเหลว ใช้ค่าเริ่มต้น")
        default_price = 75.00
        
        return {
            "current": {
                "source": "Default Estimate",
                "current_price": default_price,
                "timestamp": datetime.now(TZ).isoformat(),
                "currency": "USD/barrel",
                "commodity": "WTI Crude Oil"
            },
            "futures": self._estimate_futures_from_spot(default_price),
            "updated_at": datetime.now(TZ).strftime("%d/%m/%Y %H:%M"),
            "is_estimated": True,
            "method": "Emergency fallback (all sources failed)"
        }
