import { useState, useRef, useCallback, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";

const API = "http://localhost:8000/chat";
const RESET = "http://localhost:8000/reset";
const PORTFOLIO = "http://localhost:8000/portfolio";
const ALERTS = "http://localhost:8000/alerts";
const USAGE = "http://localhost:8000/usage";

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

  // Load portfolio positions on mount
  useEffect(() => {
    fetch(PORTFOLIO).then(r => r.json()).then(d => {
      if (d.positions) setPositions(d.positions);
    }).catch(() => { /* backend not running yet */ });
  }, []);

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

      {/* My positions */}
      {positions.length > 0 && (
        <div className="positions">
          <span className="positions-label">My Positions</span>
          {positions.map((p) => {
            const pnl = p.unrealized_pnl_pct;
            return (
              <button
                key={p.symbol}
                className={`pos-chip ${pnl >= 0 ? "pos-up" : "pos-down"}`}
                onClick={() => askPosition(p)}
                disabled={loading}
                title={`${p.shares} shares @ ${p.cost_basis} → click to analyze`}
              >
                {p.symbol}
                <span className="pos-pnl">
                  {pnl >= 0 ? "+" : ""}{pnl.toFixed(1)}%
                </span>
              </button>
            );
          })}
        </div>
      )}

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
