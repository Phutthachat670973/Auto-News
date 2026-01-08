# =============================================================================
# WTI FUTURES MODULE - Real Market Data
# =============================================================================
class WTIFuturesFetcher:
    """ดึงข้อมูลราคา WTI Futures จริงจากตลาด"""
    
    def __init__(self, api_key: str):
        """Initialize WTI Futures Fetcher"""
        self.eia_api_key = api_key.strip() if api_key else None
        self.eia_base_url = "https://api.eia.gov/v2"
        
    def fetch_current_wti_price(self) -> tuple:
        """ดึงราคา WTI ปัจจุบัน (Spot Price)"""
        
        # Method 1: Try Yahoo Finance API (Free, Real-time)
        try:
            print("[WTI] กำลังดึงราคา WTI จาก Yahoo Finance...")
            url = "https://query1.finance.yahoo.com/v8/finance/chart/CL=F"
            params = {
                "interval": "1d",
                "range": "1d"
            }
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            quote = data['chart']['result'][0]['meta']
            current_price = float(quote.get('regularMarketPrice', 0))
            
            if current_price > 0:
                print(f"[WTI/Yahoo] ✓ ราคา WTI: ${current_price:.2f}/barrel")
                return current_price, "Yahoo Finance"
                
        except Exception as e:
            print(f"[WTI/Yahoo] ข้อผิดพลาด: {str(e)}")
        
        # Method 2: Try EIA API (if available)
        if self.eia_api_key:
            try:
                print("[WTI] กำลังดึงราคา WTI จาก EIA...")
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
                
                response = requests.get(url, params=params, timeout=15)
                response.raise_for_status()
                data = response.json()
                
                response_data = data['response']['data']
                if response_data:
                    current_price = float(response_data[0]['value'])
                    print(f"[WTI/EIA] ✓ ราคา WTI: ${current_price:.2f}/barrel")
                    return current_price, "EIA"
                    
            except Exception as e:
                print(f"[WTI/EIA] ข้อผิดพลาด: {str(e)}")
        
        # Method 3: Fallback - scrape from investing.com
        try:
            print("[WTI] กำลังดึงราคา WTI จาก Investing.com...")
            url = "https://www.investing.com/commodities/crude-oil"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            
            # Parse HTML to extract price (simple regex)
            import re
            price_match = re.search(r'data-test="instrument-price-last"[^>]*>([0-9,.]+)', response.text)
            if price_match:
                price_str = price_match.group(1).replace(',', '')
                current_price = float(price_str)
                print(f"[WTI/Investing] ✓ ราคา WTI: ${current_price:.2f}/barrel")
                return current_price, "Investing.com"
                
        except Exception as e:
            print(f"[WTI/Investing] ข้อผิดพลาด: {str(e)}")
        
        raise Exception("❌ ไม่สามารถดึงราคา WTI ได้จากทุก source")
    
    def fetch_real_futures_prices(self, current_price: float) -> List[Dict]:
        """ดึงราคา WTI Futures จริงจากตลาด"""
        
        # Method 1: Try Yahoo Finance for futures contracts
        try:
            print("[WTI] กำลังดึงราคา Futures จาก Yahoo Finance...")
            
            now = datetime.now(TZ)
            futures_data = []
            
            # WTI Futures symbols on Yahoo Finance (next 12 months)
            # Format: CLF26.NYM, CLG26.NYM, etc.
            month_codes = {
                1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
                7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'
            }
            
            fetched_count = 0
            for i in range(12):
                months_ahead = i + 1
                future_date = now + timedelta(days=30 * months_ahead)
                month_code = month_codes[future_date.month]
                year_code = str(future_date.year)[-2:]
                
                symbol = f"CL{month_code}{year_code}.NYM"
                
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                    params = {"interval": "1d", "range": "1d"}
                    
                    response = requests.get(url, params=params, timeout=5)
                    response.raise_for_status()
                    data = response.json()
                    
                    quote = data['chart']['result'][0]['meta']
                    future_price = float(quote.get('regularMarketPrice', 0))
                    
                    if future_price > 0:
                        change = future_price - current_price
                        change_pct = (change / current_price) * 100
                        
                        futures_data.append({
                            "month": future_date.strftime("%b %Y"),
                            "contract": f"{month_code}{year_code}",
                            "price": round(future_price, 2),
                            "change": round(change, 2),
                            "change_pct": round(change_pct, 2)
                        })
                        
                        fetched_count += 1
                        
                except Exception:
                    continue
                
                # Rate limiting
                time.sleep(0.2)
            
            if fetched_count >= 6:  # ถ้าได้อย่างน้อย 6 เดือน
                print(f"[WTI/Yahoo] ✓ ดึงราคา Futures สำเร็จ {fetched_count} สัญญา")
                return futures_data, False  # False = ข้อมูลจริง
                
        except Exception as e:
            print(f"[WTI/Futures] ข้อผิดพลาด: {str(e)}")
        
        # Fallback: Use estimation model (but mark as estimated)
        print("[WTI] ใช้การประมาณการราคา Futures...")
        return self._estimate_futures_prices(current_price), True
    
    def _estimate_futures_prices(self, current_price: float) -> List[Dict]:
        """ประมาณการราคา Futures (Fallback method)"""
        futures_data = []
        now = datetime.now(TZ)
        
        # Contango structure พื้นฐาน
        for i in range(12):
            months_ahead = i + 1
            future_date = now +
