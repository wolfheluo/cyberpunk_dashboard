({
  NAME: "1s Momentum & OBI Scalping",
  DESCRIPTION: "結合秒級動能與盤口訂單流 (OBI)。當價格在 3 秒內急漲 > 0.05% 且 OBI > +0.6 時買入；急跌且 OBI < -0.6 時賣出。",
  
  // 自定義狀態：用於儲存短時間內的歷史 Tick，以模擬秒 K 線動能
  tickHistory: [],
  
  evaluate: function (ticker, indicators) {
    // 1. 取得當前時間戳與即時價格
    var currentTime = Date.now();
    var currentPrice = ticker.price;
    
    // 2. 獲取盤口不對稱指標 (OBI: Order Book Imbalance)
    // 參考 strategy_params (2).csv 中的定義，數值範圍為 -1 (賣盤極強) 到 1 (買盤極強)
    var obi = (ticker.book && ticker.book.imbalance !== undefined) ? ticker.book.imbalance : 0;
    
    // 將最新一筆報價寫入歷史紀錄
    this.tickHistory.push({ time: currentTime, price: currentPrice });
    
    // 過濾掉超過 3,000 毫秒 (3秒) 的舊數據，只保留極短線資料
    this.tickHistory = this.tickHistory.filter(function(tick) {
      return (currentTime - tick.time) <= 3000;
    });
    
    // 剛啟動時若資料量不足，則先回傳 WAIT 等待數據累積
    if (this.tickHistory.length < 2) {
      return { 
        signal: "WAIT", 
        confidence: 0, 
        factors: { reason: "Warming up short-term data" } 
      };
    }
    
    // 3. 計算短線微觀動量 (當前價格 vs 3 秒前價格的變化百分比)
    var oldestTick = this.tickHistory[0];
    var priceChangePct = ((currentPrice - oldestTick.price) / oldestTick.price) * 100;
    
    // 4. 設定進出場門檻 (暫不考慮手續費與滑點)
    var MOMENTUM_THRESHOLD = 0.05; // 3 秒內價格變動需超過 0.05%
    var OBI_THRESHOLD = 0.6;       // 盤口買賣壓強度需超過 0.6
    
    var isUpwardBurst = priceChangePct >= MOMENTUM_THRESHOLD;
    var isDownwardBurst = priceChangePct <= -MOMENTUM_THRESHOLD;
    
    var isBuyWallStrong = obi >= OBI_THRESHOLD;
    var isSellWallStrong = obi <= -OBI_THRESHOLD;
    
    // --- 決策邏輯 ---
    
    // 做多 (BUY)：發現秒級急漲，同時盤口買單極為厚實
    if (isUpwardBurst && isBuyWallStrong) {
      return { 
        signal: "BUY", 
        confidence: 90, 
        factors: { 
          momentum_3s_pct: priceChangePct, 
          obi: obi,
          trigger_price: currentPrice
        } 
      };
    }
    
    // 做空/平倉 (SELL)：發現秒級急跌，同時盤口湧現大量賣單
    if (isDownwardBurst && isSellWallStrong) {
      return { 
        signal: "SELL", 
        confidence: 90, 
        factors: { 
          momentum_3s_pct: priceChangePct, 
          obi: obi,
          trigger_price: currentPrice
        } 
      };
    }
    
    // 不符合爆發條件，繼續持有或觀望 (HOLD)
    return { 
      signal: "HOLD", 
      confidence: 50, 
      factors: { 
        momentum_3s_pct: priceChangePct, 
        obi: obi 
      } 
    };
  }
})