import { useEffect, useMemo, useState } from "react";
import GraphPanel from "./GraphPanel";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const EMPTY_GRAPH = {
  nodes: [],
  edges: [],
  node_count: 0,
  edge_count: 0,
};

export default function App() {
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [graph, setGraph] = useState(EMPTY_GRAPH);
  const [inspectedItem, setInspectedItem] = useState(null);
  const [sessionName, setSessionName] = useState("");
  const [useDedicatedDb, setUseDedicatedDb] = useState(false);
  const [message, setMessage] = useState("");
  const [sessionWebLimit, setSessionWebLimit] = useState("");
  const [messageWebLimit, setMessageWebLimit] = useState("");
  const [activity, setActivity] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void refreshSessions();
  }, []);

  const turns = activeSession?.turns || [];
  const graphSummary = useMemo(
    () => `${graph.node_count || 0} nodes, ${graph.edge_count || 0} edges`,
    [graph],
  );

  async function refreshSessions() {
    const response = await fetch(`${API_BASE}/sessions`);
    const payload = await response.json();
    setSessions(payload);
  }

  async function loadSession(sessionId) {
    const [detailResponse, graphResponse] = await Promise.all([
      fetch(`${API_BASE}/sessions/${sessionId}`),
      fetch(`${API_BASE}/sessions/${sessionId}/graph`),
    ]);
    const detail = await detailResponse.json();
    const graphPayload = await graphResponse.json();
    setActiveSession(detail);
    setGraph(graphPayload);
    setSessionWebLimit(detail.web_tool_call_limit ?? "");
    setMessageWebLimit(detail.web_tool_call_limit ?? "");
    setInspectedItem(null);
    setActivity([]);
    setError("");
  }

  async function openSession(event) {
    event.preventDefault();
    setError("");
    const response = await fetch(`${API_BASE}/sessions/open`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: sessionName,
        use_dedicated_db: useDedicatedDb,
        web_tool_call_limit: toNullableNumber(sessionWebLimit),
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setError(readError(payload));
      return;
    }
    setSessionName("");
    await refreshSessions();
    await loadSession(payload.session_id);
  }

  async function saveSessionSettings() {
    if (!activeSession) {
      return;
    }
    const response = await fetch(`${API_BASE}/sessions/${activeSession.session_id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        web_tool_call_limit: toNullableNumber(sessionWebLimit),
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setError(readError(payload));
      return;
    }
    setActiveSession(payload);
    await refreshSessions();
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (!activeSession || !message.trim() || isSending) {
      return;
    }
    setIsSending(true);
    setError("");
    setActivity([]);

    const response = await fetch(`${API_BASE}/sessions/${activeSession.session_id}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        web_tool_call_limit: toNullableNumber(messageWebLimit),
      }),
    });
    if (!response.ok || !response.body) {
      const payload = await response.json();
      setError(readError(payload));
      setIsSending(false);
      return;
    }

    await consumeSse(response.body, handleStreamEvent);
    setMessage("");
    await refreshSessions();
    await loadSession(activeSession.session_id);
    setIsSending(false);
  }

  function handleStreamEvent(eventName, payload) {
    setActivity((items) => [...items, formatActivity(eventName, payload)]);
    if (eventName === "session.message.failed") {
      setError(readError(payload));
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <h1>Doron</h1>
          <p>Research sessions with live graph memory.</p>
        </div>

        <form className="card form-stack" onSubmit={openSession}>
          <label>
            Session name
            <input value={sessionName} onChange={(event) => setSessionName(event.target.value)} required />
          </label>
          <label className="checkbox">
            <input
              checked={useDedicatedDb}
              type="checkbox"
              onChange={(event) => setUseDedicatedDb(event.target.checked)}
            />
            Use dedicated project DB
          </label>
          <label>
            Session web limit
            <input
              type="number"
              min="0"
              value={sessionWebLimit}
              onChange={(event) => setSessionWebLimit(event.target.value)}
              placeholder="shared default"
            />
          </label>
          <button type="submit">Open or resume</button>
        </form>

        <div className="card session-list">
          <h2>Sessions</h2>
          {sessions.map((session) => (
            <button
              className={`session-item ${activeSession?.session_id === session.session_id ? "active" : ""}`}
              key={session.session_id}
              onClick={() => loadSession(session.session_id)}
              type="button"
            >
              <span>{session.name}</span>
              <small>{session.uses_dedicated_db ? "Dedicated DB" : "Shared DB"}</small>
            </button>
          ))}
        </div>
      </aside>

      <main className="workspace">
        <section className="chat-column">
          <div className="chat-header card">
            <div>
              <h2>{activeSession?.name || "No session selected"}</h2>
              <p>{activeSession?.db_path || "Open a session to begin."}</p>
            </div>
            {activeSession ? (
              <div className="settings-inline">
                <label>
                  Session web limit
                  <input
                    type="number"
                    min="0"
                    value={sessionWebLimit}
                    onChange={(event) => setSessionWebLimit(event.target.value)}
                  />
                </label>
                <button onClick={saveSessionSettings} type="button">
                  Save
                </button>
              </div>
            ) : null}
          </div>

          <div className="chat-log card">
            {turns.map((turn) => (
              <article className={`turn ${turn.role}`} key={turn.message_id}>
                <header>
                  <strong>{turn.role === "user" ? "You" : "Doron"}</strong>
                  <span>{turn.web_tool_call_limit_used ?? "-"}</span>
                </header>
                <pre>{turn.content}</pre>
              </article>
            ))}
          </div>

          <div className="activity card">
            <h3>Live activity</h3>
            <div className="activity-log">
              {activity.map((item, index) => (
                <div className="activity-line" key={`${index}-${item}`}>
                  {item}
                </div>
              ))}
            </div>
          </div>

          <form className="composer card" onSubmit={sendMessage}>
            <textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              placeholder="Ask Doron to research, compare, inspect, or write into the graph..."
              rows={5}
            />
            <div className="composer-controls">
              <label>
                Message web limit
                <input
                  type="number"
                  min="0"
                  value={messageWebLimit}
                  onChange={(event) => setMessageWebLimit(event.target.value)}
                  placeholder="use session default"
                />
              </label>
              <button disabled={!activeSession || isSending} type="submit">
                {isSending ? "Running..." : "Send"}
              </button>
            </div>
          </form>
          {error ? <div className="error-banner">{error}</div> : null}
        </section>

        <section className="graph-column">
          <div className="card graph-header">
            <div>
              <h2>Living graph</h2>
              <p>{graphSummary}</p>
            </div>
          </div>
          <div className="card graph-wrap">
            <GraphPanel graph={graph} onInspect={setInspectedItem} />
          </div>
          <div className="card inspector">
            <h3>Metadata inspector</h3>
            <pre>{inspectedItem ? JSON.stringify(inspectedItem, null, 2) : "Select a node or edge."}</pre>
          </div>
        </section>
      </main>
    </div>
  );
}

async function consumeSse(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";
    for (const part of parts) {
      const event = parseSseEvent(part);
      if (event) {
        onEvent(event.event, event.data);
      }
    }
  }
}

function parseSseEvent(block) {
  const lines = block.split("\n");
  let event = "message";
  const data = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    }
    if (line.startsWith("data:")) {
      data.push(line.slice(5).trim());
    }
  }
  if (!data.length) {
    return null;
  }
  return { event, data: JSON.parse(data.join("\n")) };
}

function formatActivity(eventName, payload) {
  if (eventName === "tool.started") {
    return `Tool started: ${resolveToolName(payload)}`;
  }
  if (eventName === "tool.completed") {
    return `Tool ${payload.ok ? "ok" : "failed"}: ${resolveToolName(payload)} - ${resolveToolSummary(payload)}`;
  }
  if (eventName === "mission.started") {
    return `Mission started: ${payload.trace_id}`;
  }
  if (eventName === "mission.progress") {
    return `Progress: ${payload.phase || "update"} ${payload.message || ""}`.trim();
  }
  if (eventName === "session.message.completed") {
    return "Assistant reply completed.";
  }
  if (eventName === "session.graph.updated") {
    return "Graph snapshot refreshed.";
  }
  return eventName;
}

function readError(payload) {
  if (payload?.detail?.message) {
    return payload.detail.message;
  }
  if (payload?.error?.message) {
    return payload.error.message;
  }
  if (payload?.message) {
    return payload.message;
  }
  return "Request failed.";
}

function toNullableNumber(value) {
  if (value === "" || value === null || value === undefined) {
    return null;
  }
  return Number(value);
}

function resolveToolName(payload) {
  if (typeof payload?.name === "string" && payload.name) {
    return payload.name;
  }
  if (typeof payload?.metadata?.name === "string" && payload.metadata.name) {
    return payload.metadata.name;
  }
  return "tool";
}

function resolveToolSummary(payload) {
  if (typeof payload?.error_message === "string" && payload.error_message) {
    if (typeof payload?.error_type === "string" && payload.error_type) {
      return `${payload.error_type}: ${payload.error_message}`;
    }
    return payload.error_message;
  }
  if (typeof payload?.result_summary === "string" && payload.result_summary) {
    return payload.result_summary;
  }
  return "completed";
}
