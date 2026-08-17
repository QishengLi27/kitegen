import { useState, useRef, useCallback, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

const API = "http://localhost:8000/chat";
const RESET = "http://localhost:8000/reset";
const PORTFOLIO = "http://localhost:8000/portfolio";
const ALERTS = "http://localhost:8000/alerts";
const USAGE = "http://localhost:8000/usage";
const PAPER = "http://localhost:8000/paper";

function Markdown({ text, streaming = false }) {
  return (
    <div className={`markdown${streaming ? " streaming" : ""}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      {streaming && <span className="cursor" />}
    </div>
  );
}
const SUGGESTIONS = [
  "Moutai outlook", "Tencent", "Tesla vs BYD",
  "CATL", "Alibaba HK",
];

const STAGE_LABELS = {
  research: "Research",
  strategy: "Strategy",
  advice: "Advice",
};
const STAGE_ICONS = { research: "🔎", strategy: "🎯", advice: "💼" };

export default function App() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [stages, setStages] = useState([]);       // in-progress stage list
  const [adviceText, setAdviceText] = useState(""); // in-progress final advice
  const [positions, setPositions] = useState([]);   // portfolio positions
  const [portfolio, setPortfolio] = useState(null); // equity + cash summary
  const [portfolioOpen, setPortfolioOpen] = useState(true);
  // Position CRUD form: null = hidden, "add" = new, {symbol} = editing that position
  const [posForm, setPosForm] = useState(null);
  const [posFormData, setPosFormData] = useState({ symbol: "", shares: "", cost_basis: "", stop_loss: "", take_profit: "" });
  const [posFormBusy, setPosFormBusy] = useState(false);
  const [alerts, setAlerts] = useState([]);         // monitor alerts
  const [alertsOpen, setAlertsOpen] = useState(true);
  const [briefingLoading, setBriefingLoading] = useState(false);
  const [expandedBriefing, setExpandedBriefing] = useState(null);  // id of the expanded briefing
  const [threadId] = useState(() => "chat-" + Math.random().toString(36).slice(2, 8));
  const bottomRef = useRef(null);
  const inputRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); },
    [messages, stages, adviceText]);

  // Load portfolio on mount + poll every 60s for live prices
  const refreshPortfolio = useCallback(() => {
    fetch(PORTFOLIO).then(r => r.json()).then(d => {
      if (d.positions) {
        setPositions(d.positions);
        setPortfolio({ equity: d.equity, cash: d.cash });
      }
    }).catch(() => { /* backend not running yet */ });
  }, []);

  useEffect(() => {
    refreshPortfolio();
    const timer = setInterval(refreshPortfolio, 60000);
    return () => clearInterval(timer);
  }, [refreshPortfolio]);

  // ── Position CRUD ────────────────────────────────────────────────────────

  const openAddForm = () => {
    setPosForm("add");
    setPosFormData({ symbol: "", shares: "", cost_basis: "", stop_loss: "", take_profit: "" });
  };

  const openEditForm = (p) => {
    setPosForm(p.symbol);
    setPosFormData({
      symbol: p.symbol,
      shares: String(p.shares),
      cost_basis: String(p.cost_basis),
      stop_loss: p.stop_loss != null ? String(p.stop_loss) : "",
      take_profit: p.take_profit != null ? String(p.take_profit) : "",
    });
  };

  const submitPosForm = async () => {
    const body = {
      symbol: posFormData.symbol.trim().toUpperCase(),
      shares: parseFloat(posFormData.shares),
      cost_basis: parseFloat(posFormData.cost_basis),
      stop_loss: posFormData.stop_loss,
      take_profit: posFormData.take_profit,
    };
    if (!body.symbol || !body.shares || !body.cost_basis) return;

    setPosFormBusy(true);
    try {
      await fetch(`${PORTFOLIO}/positions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setPosForm(null);
      refreshPortfolio();
    } catch { /* ignore */ }
    setPosFormBusy(false);
  };

  const deletePosition = async (symbol) => {
    if (!window.confirm(`Remove ${symbol} from your portfolio?`)) return;
    try {
      await fetch(`${PORTFOLIO}/positions/${symbol}`, { method: "DELETE" });
      refreshPortfolio();
    } catch { /* ignore */ }
  };

  // Load monitor alerts + poll every 30s
  useEffect(() => {
    const loadAlerts = () => {
      fetch(ALERTS).then(r => r.json()).then(d => {
        if (d.alerts) setAlerts(d.alerts);
      }).catch(() => { /* backend not running yet */ });
    };
    loadAlerts();
    const timer = setInterval(loadAlerts, 30000);
    return () => clearInterval(timer);
  }, []);

  // Paper trading account + config
  const [paper, setPaper] = useState(null);
  const [paperOpen, setPaperOpen] = useState(false);
  const [paperConfig, setPaperConfig] = useState(null); // form draft
  const [paperBusy, setPaperBusy] = useState(false);
  const [paperResult, setPaperResult] = useState(null); // last tick summary

  const refreshPaper = useCallback(() => {
    fetch(PAPER).then(r => r.json()).then(d => {
      setPaper(d);
      if (d.config && !paperConfig) setPaperConfig(d.config);
    }).catch(() => { /* backend not running yet */ });
  }, [paperConfig]);

  useEffect(() => {
    refreshPaper();
    const timer = setInterval(refreshPaper, 30000);
    return () => clearInterval(timer);
  }, [refreshPaper]);

  const runPaperTick = async () => {
    setPaperBusy(true);
    setPaperResult(null);
    try {
      const r = await fetch(`${PAPER}/tick`, { method: "POST" });
      const d = await r.json();
      setPaperResult(d);
      refreshPaper();
    } catch { /* ignore */ }
    setPaperBusy(false);
  };

  const resetPaper = async () => {
    if (!window.confirm("Reset the paper account to initial capital?")) return;
    await fetch(`${PAPER}/reset`, { method: "POST" }).catch(() => {});
    refreshPaper();
  };

  const savePaperConfig = async () => {
    await fetch(`${PAPER}/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(paperConfig),
    }).catch(() => {});
    refreshPaper();
  };
  // Load LLM usage stats + poll every 30s
  const [usage, setUsage] = useState(null);
  useEffect(() => {
    const loadUsage = () => {
      fetch(USAGE).then(r => r.json()).then(d => {
        if (d.total_tokens != null) setUsage(d);
      }).catch(() => { /* backend not running yet */ });
    };
    loadUsage();
    const timer = setInterval(loadUsage, 30000);
    return () => clearInterval(timer);
  }, []);

  const ackAlert = (id) => {
    fetch(`${ALERTS}/${id}/ack`, { method: "POST" }).catch(() => {});
    setAlerts((prev) => prev.map(a => a.id === id ? { ...a, acknowledged: true } : a));
  };

  const triggerBriefing = async () => {
    setBriefingLoading(true);
    try {
      await fetch(`${API.replace("/chat", "")}/briefing`, { method: "POST" });
      const d = await fetch(ALERTS).then(r => r.json());
      if (d.alerts) setAlerts(d.alerts);
    } catch { /* ignore */ }
    setBriefingLoading(false);
  };

  const send = useCallback(async (text) => {
    const msg = (text || input).trim();
    if (!msg || loading) return;

    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setInput("");
    setLoading(true);
    setStages([]);
    setAdviceText("");

    // Declared outside try so the abort path can save partial results
    let finalStages = [];
    let finalAdvice = "";
    let errorMsg = null;

    try {
      const controller = new AbortController();
      abortRef.current = controller;

      const resp = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg, thread_id: threadId }),
        signal: controller.signal,
      });

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const applyEvent = (data) => {
        if (data.type === "stage_started") {
          finalStages = [...finalStages, { stage: data.stage, content: "", done: false }];
          setStages([...finalStages]);
        } else if (data.type === "stage_output") {
          finalStages = finalStages.map(s =>
            s.stage === data.stage ? { ...s, content: s.content + data.content } : s);
          setStages([...finalStages]);
        } else if (data.type === "stage_done") {
          finalStages = finalStages.map(s =>
            s.stage === data.stage ? { ...s, done: true } : s);
          setStages([...finalStages]);
        } else if (data.type === "token") {
          finalAdvice += data.content;
          setAdviceText(finalAdvice);
        } else if (data.type === "error") {
          errorMsg = data.message;
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (const part of parts) {
          for (const line of part.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            try { applyEvent(JSON.parse(line.slice(6))); } catch { /* skip */ }
          }
        }
      }

      // Push the completed assistant message
      if (errorMsg) {
        setMessages((prev) => [...prev, { role: "system", content: "❌ " + errorMsg }]);
      } else if (finalAdvice || finalStages.length) {
        setMessages((prev) => [...prev, {
          role: "assistant",
          stages: finalStages,
          content: finalAdvice,
        }]);
      }
    } catch (e) {
      if (e.name === "AbortError") {
        // User pressed stop — keep whatever was generated so far
        if (finalStages.length || finalAdvice) {
          setMessages((prev) => [...prev, {
            role: "assistant",
            stages: finalStages,
            content: finalAdvice,
            stopped: true,
          }]);
        } else {
          setMessages((prev) => [...prev, { role: "system", content: "⏹ Generation stopped" }]);
        }
      } else {
        setMessages((prev) => [...prev, {
          role: "system",
          content: "❌ Connection failed. Is the backend running on port 8000?",
        }]);
      }
    } finally {
      setLoading(false);
      setStages([]);
      setAdviceText("");
      abortRef.current = null;
    }
  }, [input, loading, threadId]);

  const askPosition = useCallback((pos) => {
    send(`Analyze my position in ${pos.symbol} with current price levels`);
  }, [send]);

  const stop = () => {
    // Abort the in-flight request. The backend cancels the pipeline;
    // partial results are preserved in the message list.
    abortRef.current?.abort();
  };

  const reset = async () => {
    setMessages([]);
    await fetch(RESET, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ thread_id: threadId }),
    }).catch(() => {});
    inputRef.current?.focus();
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="app">
      <header>
        <span className="logo">📈</span>
        <div>
          <h1>Stock Analyst</h1>
          <span className="subtitle">3-agent pipeline · Research → Strategy → Advice</span>
        </div>
        {messages.length > 0 && (
          <button className="reset-btn" onClick={reset}>New Chat</button>
        )}
      </header>

      {/* Portfolio panel */}
      {(positions.length > 0 || posForm === "add") && (
        <div className="portfolio-panel">
          <div className="portfolio-head" onClick={() => setPortfolioOpen(!portfolioOpen)}>
            <span>💼 My Positions</span>
            {portfolio && positions.length > 0 && (
              <span className="portfolio-equity">
                equity <strong>${portfolio.equity?.toLocaleString()}</strong>
                {" · "}cash <strong>${portfolio.cash?.toLocaleString()}</strong>
              </span>
            )}
            <button
              className="add-pos-btn"
              onClick={(e) => { e.stopPropagation(); setPortfolioOpen(true); openAddForm(); }}
            >
              + Add
            </button>
            <span className="alerts-toggle">{portfolioOpen ? "▾" : "▸"}</span>
          </div>
          {portfolioOpen && (
            <>
              <div className="portfolio-table">
                <div className="portfolio-row portfolio-row-head">
                  <span>Symbol</span>
                  <span className="num">Shares</span>
                  <span className="num">Cost</span>
                  <span className="num">Price</span>
                  <span className="num">P&L</span>
                  <span className="num">Weight</span>
                  <span className="levels">Stop / Target</span>
                  <span className="row-actions" />
                </div>
                {positions.map((p) => {
                  const pnl = p.unrealized_pnl_pct;
                  const pnlAbs = p.unrealized_pnl;
                  return (
                    <div
                      key={p.symbol}
                      className="portfolio-row clickable"
                      onClick={() => askPosition(p)}
                      title="Click to analyze"
                    >
                      <span className="pos-symbol">{p.symbol}</span>
                      <span className="num">{p.shares}</span>
                      <span className="num">{p.cost_basis}</span>
                      <span className="num">{p.current_price}</span>
                      <span className={`num ${pnl >= 0 ? "pos-up" : "pos-down"}`}>
                        {pnl >= 0 ? "+" : ""}{pnl.toFixed(1)}%
                        <span className="pnl-abs">({pnlAbs >= 0 ? "+" : ""}{pnlAbs.toFixed(0)})</span>
                      </span>
                      <span className="num dim">{(p.weight * 100).toFixed(1)}%</span>
                      <span className="levels dim">
                        {p.stop_loss ? <span className="stop-tag">S {p.stop_loss}</span> : "—"}
                        {" / "}
                        {p.take_profit ? <span className="target-tag">T {p.take_profit}</span> : "—"}
                      </span>
                      <span className="row-actions">
                        <button
                          className="row-btn"
                          title="Edit"
                          onClick={(e) => { e.stopPropagation(); openEditForm(p); }}
                        >✎</button>
                        <button
                          className="row-btn danger"
                          title="Delete"
                          onClick={(e) => { e.stopPropagation(); deletePosition(p.symbol); }}
                        >✕</button>
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Add / Edit position form */}
              {posForm && (
                <div className="pos-form">
                  <div className="pos-form-title">
                    {posForm === "add" ? "Add Position" : `Edit ${posForm}`}
                  </div>
                  <div className="pos-form-row">
                    <input
                      placeholder="Symbol (e.g. AAPL, 600519.SS)"
                      value={posFormData.symbol}
                      onChange={(e) => setPosFormData({ ...posFormData, symbol: e.target.value })}
                      disabled={posForm !== "add"}
                      autoFocus
                    />
                    <input
                      placeholder="Shares"
                      type="number"
                      value={posFormData.shares}
                      onChange={(e) => setPosFormData({ ...posFormData, shares: e.target.value })}
                    />
                    <input
                      placeholder="Cost basis"
                      type="number"
                      step="any"
                      value={posFormData.cost_basis}
                      onChange={(e) => setPosFormData({ ...posFormData, cost_basis: e.target.value })}
                    />
                  </div>
                  <div className="pos-form-row">
                    <input
                      placeholder="Stop loss (optional)"
                      type="number"
                      step="any"
                      value={posFormData.stop_loss}
                      onChange={(e) => setPosFormData({ ...posFormData, stop_loss: e.target.value })}
                    />
                    <input
                      placeholder="Take profit (optional)"
                      type="number"
                      step="any"
                      value={posFormData.take_profit}
                      onChange={(e) => setPosFormData({ ...posFormData, take_profit: e.target.value })}
                    />
                    <button className="form-save" onClick={submitPosForm} disabled={posFormBusy}>
                      {posFormBusy ? "..." : "Save"}
                    </button>
                    <button className="form-cancel" onClick={() => setPosForm(null)}>Cancel</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Paper trading panel */}
      <div className="portfolio-panel">
        <div className="portfolio-head" onClick={() => setPaperOpen(!paperOpen)}>
          <span>📊 Paper Trading</span>
          {paper && (
            <span className="portfolio-equity">
              cash <strong>${paper.cash?.toLocaleString()}</strong>
              {" · "}positions <strong>{paper.positions?.length}</strong>
              {" · "}trades <strong>{paper.trades?.length}</strong>
            </span>
          )}
          <span className="alerts-toggle">{paperOpen ? "▾" : "▸"}</span>
        </div>

        {paperOpen && (
          <div className="paper-body">
            {/* Equity curve */}
            {paper?.snapshots?.length > 1 && <EquityCurve snapshots={paper.snapshots} />}

            {/* Last tick result */}
            {paperResult && (
              <div className="paper-result">
                {paperResult.status === "ok" ? (
                  <>
                    {paperResult.executed?.length > 0
                      ? `Executed: ${paperResult.executed.map(t =>
                          `${t.action} ${t.symbol} ${t.shares}@${t.price}`).join("; ")}`
                      : "No trades this tick."}
                    {paperResult.blocked?.length > 0 &&
                      ` · Blocked: ${paperResult.blocked.map(b => `${b.symbol}: ${b.reason}`).join("; ")}`}
                  </>
                ) : paperResult.status === "skipped" ? (
                  <>
                    ⏸ Skipped: {paperResult.reason}.{" "}
                    <button className="run-anyway" onClick={async () => {
                      setPaperBusy(true);
                      const r = await fetch(`${PAPER}/tick?force=1`, { method: "POST" });
                      setPaperResult(await r.json());
                      refreshPaper();
                      setPaperBusy(false);
                    }}>Run anyway</button>
                  </>
                ) : (
                  `Error: ${paperResult.message || paperResult.status}`
                )}
              </div>
            )}

            {/* Positions */}
            {paper?.positions?.length > 0 && (
              <div className="paper-table">
                <div className="paper-row paper-row-head">
                  <span>Symbol</span><span className="num">Shares</span>
                  <span className="num">Cost</span><span className="num">Bought</span>
                </div>
                {paper.positions.map(p => (
                  <div key={p.symbol} className="paper-row">
                    <span className="pos-symbol">{p.symbol}</span>
                    <span className="num">{p.shares}</span>
                    <span className="num">{p.cost_basis}</span>
                    <span className="num dim">{p.buy_date}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Recent trades */}
            {paper?.trades?.length > 0 && (
              <div className="paper-table">
                <div className="paper-row paper-row-head">
                  <span>Time</span><span>Action</span><span>Symbol</span>
                  <span className="num">Price</span><span className="num">Qty</span>
                  <span className="num">P&L</span>
                </div>
                {paper.trades.slice(-8).reverse().map(t => (
                  <div key={t.id} className="paper-row" title={t.reason}>
                    <span className="dim">{t.timestamp.slice(5, 16)}</span>
                    <span className={t.action === "buy" ? "pos-up" : "pos-down"}>{t.action}</span>
                    <span className="pos-symbol">{t.symbol}</span>
                    <span className="num">{t.price}</span>
                    <span className="num">{t.shares}</span>
                    <span className={`num ${(t.realized_pnl ?? 0) >= 0 ? "pos-up" : "pos-down"}`}>
                      {t.realized_pnl != null ? (t.realized_pnl >= 0 ? "+" : "") + t.realized_pnl : "—"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Config + actions */}
            {paperConfig && (
              <div className="paper-config">
                <div className="pos-form-title">Config</div>
                <div className="pos-form-row">
                  <label>Capital
                    <input type="number" value={paperConfig.initial_capital}
                      onChange={e => setPaperConfig({ ...paperConfig, initial_capital: parseFloat(e.target.value) })} />
                  </label>
                  <label>Check every (min)
                    <input type="number" value={paperConfig.check_interval_min}
                      onChange={e => setPaperConfig({ ...paperConfig, check_interval_min: parseInt(e.target.value) })} />
                  </label>
                  <label>Max position %
                    <input type="number" step="0.01" value={paperConfig.max_position_pct}
                      onChange={e => setPaperConfig({ ...paperConfig, max_position_pct: parseFloat(e.target.value) })} />
                  </label>
                  <label>Stop loss %
                    <input type="number" step="0.01" value={paperConfig.stop_loss_pct}
                      onChange={e => setPaperConfig({ ...paperConfig, stop_loss_pct: parseFloat(e.target.value) })} />
                  </label>
                  <label className="checkbox-label">
                    <input type="checkbox" checked={paperConfig.t_plus_1}
                      onChange={e => setPaperConfig({ ...paperConfig, t_plus_1: e.target.checked })} />
                    T+1
                  </label>
                  <label className="checkbox-label">
                    <input type="checkbox" checked={paperConfig.trading_hours_only}
                      onChange={e => setPaperConfig({ ...paperConfig, trading_hours_only: e.target.checked })} />
                    Trading hours only
                  </label>
                </div>
                <div className="pos-form-row">
                  <label className="symbols-label">Symbols (comma separated — empty = real portfolio universe)
                    <input
                      type="text"
                      placeholder="AAPL, 600519.SS, 0700.HK"
                      value={(paperConfig.enabled_symbols || []).join(", ")}
                      onChange={e => setPaperConfig({
                        ...paperConfig,
                        enabled_symbols: e.target.value.split(",")
                          .map(s => s.trim().toUpperCase()).filter(Boolean),
                      })}
                    />
                  </label>
                </div>
                <div className="paper-actions">
                  <button className="form-save" onClick={savePaperConfig}>Save Config</button>
                  <button className="briefing-btn" onClick={runPaperTick} disabled={paperBusy}>
                    {paperBusy ? "Running..." : "▶ Run Tick"}
                  </button>
                  <button className="form-cancel" onClick={resetPaper}>Reset</button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Monitor alerts panel */}
      {(alerts.length > 0 || alertsOpen) && (
        <div className="alerts-panel">
          <div className="alerts-head" onClick={() => setAlertsOpen(!alertsOpen)}>
            <span>🔔 Alerts</span>
            {alerts.filter(a => !a.acknowledged).length > 0 && (
              <span className="alerts-count">
                {alerts.filter(a => !a.acknowledged).length} unread
              </span>
            )}
            <button
              className="briefing-btn"
              onClick={(e) => { e.stopPropagation(); triggerBriefing(); }}
              disabled={briefingLoading}
            >
              {briefingLoading ? "Generating..." : "📰 Briefing"}
            </button>
            <span className="alerts-toggle">{alertsOpen ? "▾" : "▸"}</span>
          </div>
          {alertsOpen && (
            <div className={`alerts-list${expandedBriefing ? " briefing-expanded" : ""}`}>
              {alerts.filter(a => !a.acknowledged).map(a => (
                <div key={a.id} className={`alert-item ${a.kind}`}>
                  <div className="alert-time">{a.timestamp.slice(5)}</div>
                  {a.kind === "briefing" ? (
                    <div
                      className="alert-body briefing-body"
                      onClick={() => setExpandedBriefing(expandedBriefing === a.id ? null : a.id)}
                    >
                      {expandedBriefing === a.id ? (
                        <Markdown text={a.message} />
                      ) : (
                        <span>
                          📰 {a.message.slice(0, 80)}…
                          <span className="briefing-open">Click to view full briefing ▸</span>
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="alert-body">
                      {`${a.symbol} · ${a.message.split("\n").slice(0, 1).join("")}`}
                    </div>
                  )}
                  <button className="alert-ack" onClick={() => ackAlert(a.id)}>✓</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="chat-area">
        {messages.length === 0 && !loading && (
          <div className="welcome">
            <p>👋 Ask about any stock, or click a position above for quick analysis</p>
            <div className="markets">
              <span>🇺🇸 US</span><span>🇭🇰 HK</span><span>🇨🇳 A-Share</span>
            </div>
            <p className="hint">Try:</p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} onClick={() => send(s)}>{s}</button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-role">
              {m.role === "user" ? "You" : m.role === "system" ? "" : "📊 Analyst"}
            </div>
            {m.stopped && <div className="msg-stopped">⏹ Generation stopped</div>}
            {m.stages && <StageList stages={m.stages} />}
            {m.content && <Markdown text={m.content} />}
          </div>
        ))}

        {(stages.length > 0 || adviceText) && (
          <div className="msg assistant">
            <div className="msg-role">📊 Analyst</div>
            <StageList stages={stages} />
            {adviceText && <Markdown text={adviceText} streaming />}
          </div>
        )}

        {loading && stages.length === 0 && !adviceText && (
          <div className="msg assistant">
            <div className="msg-role">📊 Analyst</div>
            <div className="msg-content loading">Thinking<span className="dots" /></div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="input-area">
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={messages.length ? "Follow-up..." : "Ask about a stock... (Moutai / Tencent / 000725 / BOE)"}
          disabled={loading}
          autoFocus
        />
        {loading ? (
          <button className="stop-btn" onClick={stop}>⏹ Stop</button>
        ) : (
          <button onClick={() => send()} disabled={!input.trim()}>Send</button>
        )}
      </div>

      <div className="footer">
        kitegen · 3-agent pipeline · session: <code>{threadId}</code>
        {usage && (
          <>
            {" · "}
            <span className="usage-stats">
              tokens <strong>{usage.total_tokens?.toLocaleString()}</strong>
              {" · "}cost <strong>${usage.total_cost?.toFixed(4)}</strong>
              {" · "}calls <strong>{usage.calls}</strong>
              {usage.by_model && Object.keys(usage.by_model).length > 0 && (
                <>{" · "}{Object.entries(usage.by_model).map(([m, s]) => `${m}: ${s.tokens.toLocaleString()}`).join(", ")}</>
              )}
            </span>
          </>
        )}
      </div>
    </div>
  );
}

/* ── Equity curve (inline SVG) ────────────────────────────────────────────── */

function EquityCurve({ snapshots }) {
  const W = 680, H = 80, PAD = 4;
  const values = snapshots.map(s => s.equity);
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;

  const points = snapshots.map((s, i) => {
    const x = PAD + (i / (snapshots.length - 1)) * (W - 2 * PAD);
    const y = PAD + (1 - (s.equity - min) / range) * (H - 2 * PAD);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");

  const first = snapshots[0].equity;
  const last = snapshots[snapshots.length - 1].equity;
  const change = ((last - first) / first) * 100;
  const color = change >= 0 ? "var(--green)" : "var(--red)";

  return (
    <div className="equity-curve">
      <div className="equity-curve-head">
        <span>Equity curve</span>
        <span style={{ color }}>
          {change >= 0 ? "+" : ""}{change.toFixed(1)}%
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
        <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" />
      </svg>
    </div>
  );
}

/* ── Stage stepper component ─────────────────────────────────────────────── */

function StageList({ stages }) {
  return (
    <div className="stage-list">
      {stages.map((s, i) => (
        <StageItem key={s.stage} stage={s} index={i} />
      ))}
    </div>
  );
}

function StageItem({ stage: s, index: i }) {
  const [open, setOpen] = useState(false);  // collapsed by default

  return (
    <div className={`stage ${s.done ? "done" : "active"}`}>
      <div className="stage-head" onClick={() => s.content && setOpen(!open)}>
        <span className="stage-num">{s.done ? "✓" : i + 1}</span>
        <span className="stage-title">
          {STAGE_ICONS[s.stage]} {STAGE_LABELS[s.stage] || s.stage}
        </span>
        {s.content && (
          <span className={`stage-toggle ${open ? "open" : ""}`}>
            {open ? "▾ Hide details" : "▸ Show details"}
          </span>
        )}
        {!s.done && <span className="stage-spinner" />}
      </div>
      {s.content && open && <Markdown text={s.content} />}
    </div>
  );
}
