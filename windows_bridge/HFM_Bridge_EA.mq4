//+------------------------------------------------------------------+
//| HFM_Bridge_EA.mq4                                                 |
//| Puente por archivos entre mt_bridge.py (Python) y MetaTrader 4.   |
//| Cada 100 ms lee MQL4\Files\aitb\req_*.json, ejecuta el comando y  |
//| responde en res_<id>.json. Sin DLL. Una instancia por terminal.   |
//+------------------------------------------------------------------+
#property copyright "AI Trading bridge"
#property version   "1.00"
#property strict

input int    TimerMs        = 100;     // Intervalo de lectura (ms)
input string Folder         = "aitb";  // Carpeta dentro de MQL4\Files
input int    MaxRequestAge  = 20;      // Segundos: descarta peticiones más antiguas (nunca se ejecutan tarde)
input int    TradeRetries   = 3;       // Reintentos ante recotización / terminal ocupado

datetime g_lastServer = 0;
datetime g_lastChange = 0;
uint     g_lastBeat   = 0;

//------------------------------ helpers ------------------------------
string D(double v, int digits = 8) { return DoubleToString(v, digits); }

string Esc(string s)
  {
   string out = "";
   int n = StringLen(s);
   for(int i = 0; i < n; i++)
     {
      ushort c = StringGetCharacter(s, i);
      if(c == '"' || c == '\\') out += "\\" + ShortToString(c);
      else if(c < 32) out += " ";
      else out += ShortToString(c);
     }
   return out;
  }

// Valor de una clave en un JSON plano {"k":"v","n":1.5} (el que escribe mt_bridge.py, sin espacios ni objetos anidados)
string J(const string &json, string key)
  {
   string k = "\"" + key + "\":";
   int p = StringFind(json, k);
   if(p < 0) return "";
   p += StringLen(k);
   int n = StringLen(json);
   if(StringGetCharacter(json, p) == '"')
     {
      string out = "";
      p++;
      while(p < n)
        {
         ushort c = StringGetCharacter(json, p);
         if(c == '\\') { p++; out += ShortToString(StringGetCharacter(json, p)); }
         else if(c == '"') break;
         else out += ShortToString(c);
         p++;
        }
      return out;
     }
   int e = p;
   while(e < n)
     {
      ushort c = StringGetCharacter(json, e);
      if(c == ',' || c == '}') break;
      e++;
     }
   return StringSubstr(json, p, e - p);
  }

double JD(const string &json, string key) { return StringToDouble(J(json, key)); }
long   JL(const string &json, string key) { return StringToInteger(J(json, key)); }

bool ReadAll(string path, string &content)
  {
   int h = FileOpen(path, FILE_READ | FILE_BIN | FILE_ANSI | FILE_SHARE_READ);
   if(h == INVALID_HANDLE) return false;
   content = FileReadString(h, (int)FileSize(h));
   FileClose(h);
   return true;
  }

// Escritura atómica: archivo temporal + renombrado
bool WriteAtomic(string name, string content)
  {
   string tmp = Folder + "\\" + name + ".tmp";
   int h = FileOpen(tmp, FILE_WRITE | FILE_BIN | FILE_ANSI);
   if(h == INVALID_HANDLE) return false;
   FileWriteString(h, content, StringLen(content));
   FileClose(h);
   return FileMove(tmp, 0, Folder + "\\" + name + ".json", FILE_REWRITE);
  }

string Fail(int err, string msg = "") { return "\"ok\":0,\"err\":" + IntegerToString(err) + ",\"msg\":\"" + Esc(msg) + "\""; }

bool TradeAllowed()
  {
   return IsConnected() && TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) && MQLInfoInteger(MQL_TRADE_ALLOWED)
          && AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) && AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
  }

bool Transient(int err) { return err == 4 || err == 135 || err == 136 || err == 137 || err == 138 || err == 146; }

void WaitContext()
  {
   for(int i = 0; i < 30 && IsTradeContextBusy(); i++) Sleep(100);
  }

//------------------------------ lifecycle ------------------------------
// Borra por lotes (nombres primero, búsqueda cerrada, luego borrado) para no recorrer la carpeta mientras se modifica
void PurgeOld(string mask)
  {
   string batch[200];
   for(int round = 0; round < 100; round++)
     {
      int n = 0;
      string name;
      long h = FileFindFirst(Folder + "\\" + mask, name);
      if(h == INVALID_HANDLE) return;
      do { batch[n] = name; n++; } while(n < 200 && FileFindNext(h, name));
      FileFindClose(h);
      int deleted = 0;
      for(int i = 0; i < n; i++) if(FileDelete(Folder + "\\" + batch[i])) deleted++;
      if(n < 200 || deleted == 0) return;
     }
  }

int OnInit()
  {
   FolderCreate(Folder);
   // peticiones que quedaron de una sesión anterior: se borran, jamás se ejecutan tarde
   PurgeOld("req_*");
   PurgeOld("res_*");
   EventSetMillisecondTimer(MathMax(50, TimerMs));
   Heartbeat();
   Print("HFM_Bridge_EA activo · carpeta MQL4\\Files\\", Folder, " · cuenta ", AccountNumber(), " ", AccountServer());
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   FileDelete(Folder + "\\status.json");
  }

void OnTick() {}

#define MAX_BATCH 25

void OnTimer()
  {
   if(TimeCurrent() != g_lastServer) { g_lastServer = TimeCurrent(); g_lastChange = TimeLocal(); }
   if(GetTickCount() - g_lastBeat >= 1000) Heartbeat();
   // 1) recoger hasta MAX_BATCH nombres y cerrar la búsqueda ANTES de tocar ningún archivo
   string names[MAX_BATCH];
   int n = 0;
   string name;
   long h = FileFindFirst(Folder + "\\req_*.json", name);
   if(h == INVALID_HANDLE) return;
   do
     {
      names[n] = name;
      n++;
     }
   while(n < MAX_BATCH && FileFindNext(h, name));
   FileFindClose(h);
   // 2) procesar: cada petición se borra al leerla (así nunca se ejecuta dos veces) y luego se atiende
   for(int i = 0; i < n; i++)
     {
      string path = Folder + "\\" + names[i], json = "";
      if(!ReadAll(path, json))
        {
         // ilegible: si lleva más de MaxRequestAge segundos, se elimina para que no se acumule
         if(TimeLocal() - (datetime)FileGetInteger(path, FILE_MODIFY_DATE) > MaxRequestAge) FileDelete(path);
         continue;
        }
      FileDelete(path);
      Handle(json);
      if(GetTickCount() - g_lastBeat >= 1000) Heartbeat();   // el latido sigue vivo durante un lote largo
     }
  }

void Heartbeat()
  {
   g_lastBeat = GetTickCount();
   string s = "{\"login\":" + IntegerToString(AccountNumber()) + ",\"server\":\"" + Esc(AccountServer()) + "\",\"company\":\"" + Esc(AccountCompany())
              + "\",\"name\":\"" + Esc(AccountName()) + "\",\"currency\":\"" + Esc(AccountCurrency()) + "\",\"connected\":" + IntegerToString(IsConnected())
              + ",\"trade_allowed\":" + IntegerToString(TradeAllowed()) + ",\"balance\":" + D(AccountBalance(), 2) + ",\"equity\":" + D(AccountEquity(), 2)
              + ",\"margin\":" + D(AccountMargin(), 2) + ",\"free_margin\":" + D(AccountFreeMargin(), 2) + ",\"leverage\":" + IntegerToString(AccountLeverage())
              + ",\"server_time\":" + IntegerToString((long)TimeCurrent()) + ",\"gmt_time\":" + IntegerToString((long)TimeGMT())
              + ",\"tick_age\":" + IntegerToString((long)(TimeLocal() - g_lastChange)) + "}";
   WriteAtomic("status", s);
  }

//------------------------------ dispatcher ------------------------------
void Handle(const string &json)
  {
   string id = J(json, "id"), cmd = J(json, "cmd"), body;
   if(id == "") return;
   long ts = JL(json, "ts");
   if(ts > 0 && (long)TimeGMT() - ts > MaxRequestAge) return;   // caducada: el puente ya dejó de esperar, no se ejecuta ni se escribe respuesta
   if(cmd == "symbols")   body = CmdSymbols();
   else if(cmd == "spec")      body = CmdSpec(J(json, "symbol"));
   else if(cmd == "candles")   body = CmdCandles(J(json, "symbol"), (int)JL(json, "tf"), (datetime)JL(json, "start"), (int)JL(json, "limit"));
   else if(cmd == "positions") body = CmdPositions();
   else if(cmd == "history")   body = CmdHistory((datetime)JL(json, "from"), (datetime)JL(json, "to"));
   else if(cmd == "open")      body = CmdOpen(J(json, "symbol"), (int)JL(json, "side"), JD(json, "lots"), JD(json, "sl"), JD(json, "tp"), J(json, "comment"), (int)JL(json, "magic"), (int)JL(json, "slippage"));
   else if(cmd == "close")     body = CmdClose((int)JL(json, "ticket"), JD(json, "lots"), (int)JL(json, "slippage"));
   else if(cmd == "modify")    body = CmdModify((int)JL(json, "ticket"), JD(json, "sl"), JD(json, "tp"));
   else body = Fail(3, "comando desconocido: " + cmd);
   WriteAtomic("res_" + id, "{\"id\":\"" + id + "\"," + body + "}");
  }

//------------------------------ market data ------------------------------
string CmdSymbols()
  {
   string s = "\"ok\":1,\"symbols\":[";
   int n = SymbolsTotal(false);
   for(int i = 0; i < n; i++) s += (i ? "," : "") + "\"" + Esc(SymbolName(i, false)) + "\"";
   return s + "]";
  }

// Trade mode by NAME: the numeric values of ENUM_SYMBOL_TRADE_MODE differ between MT4 (FULL = 2) and MT5 (FULL = 4)
string TradeModeName(string sym)
  {
   long m = SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
   if(m == SYMBOL_TRADE_MODE_FULL)      return "FULL";
   if(m == SYMBOL_TRADE_MODE_CLOSEONLY) return "CLOSEONLY";
   if(m == SYMBOL_TRADE_MODE_LONGONLY)  return "LONGONLY";
   if(m == SYMBOL_TRADE_MODE_SHORTONLY) return "SHORTONLY";
   if(m == SYMBOL_TRADE_MODE_DISABLED)  return "DISABLED";
   return "FULL";
  }

string CmdSpec(string sym)
  {
   ResetLastError();
   if(!SymbolSelect(sym, true)) return Fail(4106, "simbolo desconocido: " + sym);
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   return "\"ok\":1,\"symbol\":\"" + Esc(sym) + "\",\"digits\":" + IntegerToString(digits) + ",\"point\":" + D(MarketInfo(sym, MODE_POINT))
          + ",\"min_lot\":" + D(MarketInfo(sym, MODE_MINLOT)) + ",\"lot_step\":" + D(MarketInfo(sym, MODE_LOTSTEP)) + ",\"max_lot\":" + D(MarketInfo(sym, MODE_MAXLOT))
          + ",\"contract\":" + D(MarketInfo(sym, MODE_LOTSIZE), 2) + ",\"tick_size\":" + D(MarketInfo(sym, MODE_TICKSIZE)) + ",\"tick_value\":" + D(MarketInfo(sym, MODE_TICKVALUE))
          + ",\"trade_mode\":\"" + TradeModeName(sym) + "\",\"trade_allowed\":" + IntegerToString((int)MarketInfo(sym, MODE_TRADEALLOWED)) + ",\"stops_level\":" + IntegerToString((int)MarketInfo(sym, MODE_STOPLEVEL))
          + ",\"bid\":" + D(MarketInfo(sym, MODE_BID), digits) + ",\"ask\":" + D(MarketInfo(sym, MODE_ASK), digits) + ",\"time\":" + IntegerToString(SymbolInfoInteger(sym, SYMBOL_TIME));
  }

string CmdCandles(string sym, int tf, datetime start, int limit)
  {
   if(!SymbolSelect(sym, true)) return Fail(4106, "simbolo desconocido: " + sym);
   MqlRates r[];
   ArraySetAsSeries(r, false);
   ResetLastError();
   int n = (start > 0) ? CopyRates(sym, (ENUM_TIMEFRAMES)tf, start, limit, r) : CopyRates(sym, (ENUM_TIMEFRAMES)tf, 0, limit, r);
   if(n <= 0) return Fail(GetLastError(), "sin velas (historial descargandose)");
   int digits = (int)MarketInfo(sym, MODE_DIGITS);
   string s = "\"ok\":1,\"rows\":[";
   for(int i = 0; i < n; i++)
      s += (i ? "," : "") + "[" + IntegerToString((long)r[i].time) + "," + D(r[i].open, digits) + "," + D(r[i].high, digits) + "," + D(r[i].low, digits) + ","
           + D(r[i].close, digits) + "," + IntegerToString(r[i].tick_volume) + "]";
   return s + "]";
  }

//------------------------------ orders ------------------------------
string OrderJson(bool closed)
  {
   int digits = (int)MarketInfo(OrderSymbol(), MODE_DIGITS);
   if(digits <= 0) digits = 5;
   string s = "{\"ticket\":" + IntegerToString(OrderTicket()) + ",\"symbol\":\"" + Esc(OrderSymbol()) + "\",\"type\":" + IntegerToString(OrderType())
              + ",\"lots\":" + D(OrderLots(), 2) + ",\"open_price\":" + D(OrderOpenPrice(), digits) + ",\"sl\":" + D(OrderStopLoss(), digits) + ",\"tp\":" + D(OrderTakeProfit(), digits)
              + ",\"profit\":" + D(OrderProfit(), 2) + ",\"swap\":" + D(OrderSwap(), 2) + ",\"commission\":" + D(OrderCommission(), 2)
              + ",\"comment\":\"" + Esc(OrderComment()) + "\",\"magic\":" + IntegerToString(OrderMagicNumber()) + ",\"open_time\":" + IntegerToString((long)OrderOpenTime());
   if(closed) s += ",\"close_price\":" + D(OrderClosePrice(), digits) + ",\"close_time\":" + IntegerToString((long)OrderCloseTime());
   else       s += ",\"price\":" + D(OrderClosePrice(), digits);
   return s + "}";
  }

string CmdPositions()
  {
   string s = "\"ok\":1,\"positions\":[";
   int k = 0;
   for(int i = 0; i < OrdersTotal(); i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES) || OrderType() > OP_SELL) continue;
      s += (k++ ? "," : "") + OrderJson(false);
     }
   return s + "]";
  }

// Requiere que la pestaña «Historial de cuenta» del terminal muestre «Todo el historial»
string CmdHistory(datetime from, datetime to)
  {
   string s = "\"ok\":1,\"orders\":[";
   int k = 0;
   for(int i = OrdersHistoryTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
      int t = OrderType();
      if(t != OP_BUY && t != OP_SELL && t != 6 && t != 7) continue;
      if(OrderCloseTime() < from || OrderCloseTime() > to) continue;
      s += (k++ ? "," : "") + OrderJson(true);
     }
   return s + "]";
  }

string CmdOpen(string sym, int side, double lots, double sl, double tp, string comment, int magic, int slippage)
  {
   if(!TradeAllowed()) return Fail(IsConnected() ? 4109 : 6, "Algo Trading desactivado o terminal sin conexion");
   if(!SymbolSelect(sym, true)) return Fail(4106, "simbolo desconocido: " + sym);
   int digits = (int)MarketInfo(sym, MODE_DIGITS), ticket = -1, err = 0;
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   bool naked = false;
   for(int i = 0; i <= TradeRetries; i++)
     {
      WaitContext();
      RefreshRates();
      double price = NormalizeDouble(MarketInfo(sym, side == OP_BUY ? MODE_ASK : MODE_BID), digits);
      ResetLastError();
      ticket = OrderSend(sym, side, lots, price, slippage, naked ? 0 : sl, naked ? 0 : tp, comment, magic, 0, clrNONE);
      if(ticket > 0) break;
      err = GetLastError();
      if(err == 130 && !naked && (sl > 0 || tp > 0)) { naked = true; continue; }  // cuentas ECN: primero la orden, despues los stops
      if(!Transient(err)) break;
      Sleep(300);
     }
   if(ticket <= 0) return Fail(err, "OrderSend");
   if(naked)
     {
      bool ok = false;
      for(int i = 0; i < 5 && !ok; i++)
        {
         WaitContext();
         if(OrderSelect(ticket, SELECT_BY_TICKET)) ok = OrderModify(ticket, OrderOpenPrice(), sl, tp, 0, clrNONE);
         if(!ok) Sleep(300);
        }
      if(!ok)
        {
         // nunca se deja una posicion sin stop loss: se cierra y se informa del rechazo
         int merr = GetLastError();
         for(int i = 0; i < 5; i++)
           {
            WaitContext();
            RefreshRates();
            if(OrderSelect(ticket, SELECT_BY_TICKET) && OrderClose(ticket, OrderLots(), NormalizeDouble(MarketInfo(sym, side == OP_BUY ? MODE_BID : MODE_ASK), digits), slippage * 3, clrNONE)) break;
            Sleep(300);
           }
         return Fail(merr == 0 ? 130 : merr, "no se pudo colocar el SL/TP: orden cerrada");
        }
     }
   if(!OrderSelect(ticket, SELECT_BY_TICKET)) return "\"ok\":1,\"ticket\":" + IntegerToString(ticket) + ",\"price\":0";
   return "\"ok\":1,\"ticket\":" + IntegerToString(ticket) + ",\"price\":" + D(OrderOpenPrice(), digits) + ",\"lots\":" + D(OrderLots(), 2);
  }

// Tras un cierre parcial MT4 crea un ticket nuevo para el resto (comentario «from #ticket»)
int FindRemainder(int oldTicket, string sym, int type, int magic, datetime openTime)
  {
   string tag = "from #" + IntegerToString(oldTicket);
   int fallback = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_TRADES) || OrderTicket() == oldTicket) continue;
      if(StringFind(OrderComment(), tag) >= 0) return OrderTicket();
      if(OrderSymbol() == sym && OrderType() == type && OrderMagicNumber() == magic && OrderOpenTime() == openTime) fallback = OrderTicket();
     }
   return fallback;
  }

string CmdClose(int ticket, double lots, int slippage)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET) || OrderCloseTime() != 0 || OrderType() > OP_SELL) return Fail(4108, "la orden no esta abierta");
   if(!TradeAllowed()) return Fail(IsConnected() ? 4109 : 6, "Algo Trading desactivado o terminal sin conexion");
   string sym = OrderSymbol();
   int type = OrderType(), magic = OrderMagicNumber(), digits = (int)MarketInfo(sym, MODE_DIGITS), err = 0;
   datetime openTime = OrderOpenTime();
   double total = OrderLots();
   bool partial = lots > 0 && lots < total - 0.0000001;
   if(!partial) lots = total;
   bool ok = false;
   double price = 0;
   for(int i = 0; i <= TradeRetries && !ok; i++)
     {
      WaitContext();
      RefreshRates();
      price = NormalizeDouble(MarketInfo(sym, type == OP_BUY ? MODE_BID : MODE_ASK), digits);
      ResetLastError();
      ok = OrderClose(ticket, lots, price, slippage, clrNONE);
      if(ok) break;
      err = GetLastError();
      if(!Transient(err)) break;
      Sleep(300);
     }
   if(!ok) return Fail(err, "OrderClose");
   int remainder = partial ? FindRemainder(ticket, sym, type, magic, openTime) : 0;
   if(OrderSelect(ticket, SELECT_BY_TICKET, MODE_HISTORY)) price = OrderClosePrice();
   return "\"ok\":1,\"ticket\":" + IntegerToString(ticket) + ",\"new_ticket\":" + IntegerToString(remainder) + ",\"price\":" + D(price, digits) + ",\"lots\":" + D(lots, 2);
  }

string CmdModify(int ticket, double sl, double tp)
  {
   if(!OrderSelect(ticket, SELECT_BY_TICKET) || OrderCloseTime() != 0) return Fail(4108, "la orden no esta abierta");
   if(!TradeAllowed()) return Fail(IsConnected() ? 4109 : 6, "Algo Trading desactivado o terminal sin conexion");
   int digits = (int)MarketInfo(OrderSymbol(), MODE_DIGITS), err = 0;
   sl = NormalizeDouble(sl, digits);
   tp = (tp < 0) ? OrderTakeProfit() : NormalizeDouble(tp, digits);
   double pt = MarketInfo(OrderSymbol(), MODE_POINT);
   if(MathAbs(OrderStopLoss() - sl) < pt / 2 && MathAbs(OrderTakeProfit() - tp) < pt / 2) return "\"ok\":1,\"unchanged\":1";
   for(int i = 0; i <= TradeRetries; i++)
     {
      WaitContext();
      ResetLastError();
      if(OrderModify(ticket, OrderOpenPrice(), sl, tp, 0, clrNONE)) return "\"ok\":1";
      err = GetLastError();
      if(err == 1) return "\"ok\":1,\"unchanged\":1";
      if(!Transient(err)) break;
      Sleep(300);
      if(!OrderSelect(ticket, SELECT_BY_TICKET)) break;
     }
   return Fail(err, "OrderModify");
  }
//+------------------------------------------------------------------+
