import { useEffect, useMemo, useRef, useState } from "react";
import { JsonView, collapseAllNested, darkStyles } from "react-json-view-lite";
import { ReactMarkdown } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/github-dark.css";
import "react-json-view-lite/dist/index.css";
import GraphPanel from "./GraphPanel";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const EMPTY_GRAPH = {
  nodes: [],
  edges: [],
  node_count: 0,
  edge_count: 0,
};

const INSPECTOR_JSON_STYLES = extendJsonViewStyles(darkStyles, {
  container: "inspector-json",
  childFieldsContainer: "inspector-json-children",
  collapseIcon: "inspector-json-collapse-icon",
  expandIcon: "inspector-json-expand-icon",
  collapsedContent: "inspector-json-collapsed-content",
  label: "inspector-json-label",
  clickableLabel: "inspector-json-clickable-label",
  nullValue: "inspector-json-null-value",
  undefinedValue: "inspector-json-undefined-value",
  numberValue: "inspector-json-number-value",
  stringValue: "inspector-json-string-value",
  booleanValue: "inspector-json-boolean-value",
  otherValue: "inspector-json-other-value",
  punctuation: "inspector-json-punctuation",
});

export default function App() {
  const [sessions, setSessions] = useState([]);
  const [activeSession, setActiveSession] = useState(null);
  const [graph, setGraph] = useState(EMPTY_GRAPH);
  const [graphSearchQuery, setGraphSearchQuery] = useState("");
  const [inspectedItem, setInspectedItem] = useState(null);
  const [sessionName, setSessionName] = useState("");
  const [useDedicatedDb, setUseDedicatedDb] = useState(false);
  const [message, setMessage] = useState("");
  const [sessionWebLimit, setSessionWebLimit] = useState("");
  const [messageWebLimit, setMessageWebLimit] = useState("");
  const [activity, setActivity] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(() => {
    if (typeof window === "undefined") {
      return true;
    }
    const savedValue = window.localStorage.getItem("doron.sidebarCollapsed");
    if (savedValue === null) {
      return true;
    }
    return savedValue === "1";
  });
  const activityLogRef = useRef(null);
  const followActivityRef = useRef(true);

  useEffect(() => {
    void refreshSessions();
  }, []);

  useEffect(() => {
    window.localStorage.setItem("doron.sidebarCollapsed", isSidebarCollapsed ? "1" : "0");
  }, [isSidebarCollapsed]);

  const turns = activeSession?.turns || [];
  const pendingUserMessage = isSending ? message.trim() : "";
  const showThinkingBlock = isSending || activity.length > 0;
  const filteredGraph = useMemo(() => filterGraph(graph, graphSearchQuery), [graph, graphSearchQuery]);
  const graphSummary = useMemo(() => {
    if (!graphSearchQuery.trim()) {
      return `${graph.node_count || 0} nodes, ${graph.edge_count || 0} edges`;
    }
    return `${filteredGraph.node_count}/${graph.node_count || 0} nodes, ${filteredGraph.edge_count}/${graph.edge_count || 0} edges`;
  }, [filteredGraph, graph, graphSearchQuery]);

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

  useEffect(() => {
    if (showThinkingBlock) {
      followActivityRef.current = true;
    }
  }, [showThinkingBlock]);

  useEffect(() => {
    if (!showThinkingBlock || !followActivityRef.current) {
      return;
    }
    const container = activityLogRef.current;
    if (!container) {
      return;
    }
    container.scrollTop = container.scrollHeight;
  }, [activity, showThinkingBlock]);

  async function refreshGraph(sessionId) {
    const response = await fetch(`${API_BASE}/sessions/${sessionId}/graph`);
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    setGraph(payload);
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
    setActivity((items) => [...items, buildActivityEntry(eventName, payload)]);
    if (eventName === "session.message.failed") {
      setError(readError(payload));
    }
    if (eventName === "session.graph.updated" && payload.session_id) {
      void refreshGraph(payload.session_id);
    }
  }

  return (
    <div className={`app-shell ${isSidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      <aside className={`sidebar ${isSidebarCollapsed ? "collapsed" : ""}`}>
        <div className="sidebar-top">
          <div className="brand">
            <h1>Doron</h1>
            <p>Research sessions with live graph memory.</p>
          </div>
          <button
            className="sidebar-toggle"
            aria-expanded={!isSidebarCollapsed}
            aria-label={isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setIsSidebarCollapsed((value) => !value)}
            type="button"
          >
            <span aria-hidden="true" className="sidebar-toggle-bars">
              <span />
              <span />
              <span />
            </span>
          </button>
        </div>

        <div className="sidebar-body">
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

          <div className="chat-log">
            {turns.map((turn) => (
              <article className={`turn ${turn.role}`} key={turn.message_id}>
                <header>
                  <strong>{turn.role === "user" ? "You" : "Doron"}</strong>
                  <span>{turn.web_tool_call_limit_used ?? "-"}</span>
                </header>
                <TurnContent turn={turn} />
              </article>
            ))}
            {pendingUserMessage ? (
              <article className="turn user pending-turn" key="pending-user-message">
                <header>
                  <strong>You</strong>
                  <span>{toDisplayLimit(messageWebLimit)}</span>
                </header>
                <pre>{pendingUserMessage}</pre>
              </article>
            ) : null}
            {showThinkingBlock ? (
              <article className="turn assistant thinking-turn" key="live-activity">
                <header>
                  <strong>Doron</strong>
                  <span>{isSending ? "thinking" : "activity"}</span>
                </header>
                <div className="thinking-shell">
                  <div className="thinking-label">Live activity</div>
                  <div
                    className="activity-log"
                    onScroll={() => {
                      const container = activityLogRef.current;
                      if (!container) {
                        return;
                      }
                      const distanceFromBottom =
                        container.scrollHeight - container.scrollTop - container.clientHeight;
                      followActivityRef.current = distanceFromBottom <= 24;
                    }}
                    ref={activityLogRef}
                  >
                    {activity.map((item) =>
                      item.kind === "tool" ? (
                        <details className={`activity-item tool ${item.status}`} key={item.id}>
                          <summary>
                            <span className="activity-summary">{item.summary}</span>
                            <span className="activity-status">{item.statusLabel}</span>
                          </summary>
                          <div className="activity-panel">
                            <div className="activity-panel-heading">Parameters</div>
                            <pre>{formatJson(item.parameters)}</pre>
                            {item.reason ? (
                              <>
                                <div className="activity-panel-heading">Reason</div>
                                <pre>{item.reason}</pre>
                              </>
                            ) : null}
                          </div>
                        </details>
                      ) : (
                        <div className="activity-line" key={item.id}>
                          {item.text}
                        </div>
                      ),
                    )}
                    <div aria-hidden="true" />
                  </div>
                </div>
              </article>
            ) : null}
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
            <div className="graph-header-copy">
              <h2>Living graph</h2>
              <p>{graphSummary}</p>
            </div>
            <label className="graph-search">
              <span>Search graph</span>
              <input
                type="search"
                value={graphSearchQuery}
                onChange={(event) => setGraphSearchQuery(event.target.value)}
                placeholder="Search nodes, edges, ids, properties..."
              />
            </label>
          </div>
          <div className="card graph-wrap">
            <GraphPanel graph={filteredGraph} onInspect={setInspectedItem} />
          </div>
          <div className="card inspector">
            <MetadataInspector item={inspectedItem} />
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

function buildActivityEntry(eventName, payload) {
  const id = `${eventName}-${payload.created_at || payload.trace_id || payload.message_id || cryptoRandomId()}`;
  if (eventName === "tool.started") {
    return {
      id,
      kind: "tool",
      status: "started",
      statusLabel: "started",
      summary: `Tool started: ${resolveToolName(payload)}`,
      parameters: resolveToolParameters(payload),
    };
  }
  if (eventName === "tool.completed") {
    const status = payload.ok ? "ok" : "failed";
    return {
      id,
      kind: "tool",
      status,
      statusLabel: status,
      summary: `Tool ${status}: ${resolveToolName(payload)} - ${resolveToolSummary(payload)}`,
      parameters: resolveToolParameters(payload),
      reason: resolveToolReason(payload),
    };
  }
  if (eventName === "mission.started") {
    return { id, kind: "text", text: `Mission started: ${payload.trace_id}` };
  }
  if (eventName === "mission.progress") {
    return { id, kind: "text", text: `Progress: ${payload.phase || "update"} ${payload.message || ""}`.trim() };
  }
  if (eventName === "session.message.completed") {
    return { id, kind: "text", text: "Assistant reply completed." };
  }
  if (eventName === "session.graph.updated") {
    return { id, kind: "text", text: "Graph snapshot refreshed." };
  }
  return { id, kind: "text", text: eventName };
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

function toDisplayLimit(value) {
  if (value === "" || value === null || value === undefined) {
    return "-";
  }
  return String(value);
}

function filterGraph(graph, query) {
  const normalizedQuery = normalizeSearchText(query);
  if (!normalizedQuery) {
    return graph;
  }

  const matchingNodeIds = new Set();
  const includedNodeIds = new Set();
  const matchingEdgeIds = new Set();

  for (const node of graph.nodes) {
    if (matchesGraphItem(node, normalizedQuery, "node")) {
      matchingNodeIds.add(node.id);
      includedNodeIds.add(node.id);
    }
  }

  for (const edge of graph.edges) {
    if (matchesGraphItem(edge, normalizedQuery, "edge")) {
      matchingEdgeIds.add(edge.id);
      includedNodeIds.add(edge.source);
      includedNodeIds.add(edge.target);
    }
  }

  const nodes = graph.nodes.filter((node) => includedNodeIds.has(node.id));
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges = graph.edges.filter((edge) => {
    if (matchingEdgeIds.has(edge.id)) {
      return visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target);
    }
    return visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target);
  });

  return {
    ...graph,
    node_count: nodes.length,
    edge_count: edges.length,
    nodes,
    edges,
  };
}

function matchesGraphItem(item, normalizedQuery, type) {
  return serializeGraphItem(item, type).includes(normalizedQuery);
}

function serializeGraphItem(item, type) {
  if (type === "node") {
    return normalizeSearchText([item.id, item.label, item.kind, flattenSearchValue(item.properties)].join(" "));
  }
  return normalizeSearchText(
    [item.id, item.label, item.source, item.target, flattenSearchValue(item.properties)].join(" "),
  );
}

function flattenSearchValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => flattenSearchValue(item)).join(" ");
  }
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => `${key} ${flattenSearchValue(item)}`)
      .join(" ");
  }
  return String(value);
}

function normalizeSearchText(value) {
  return String(value || "").trim().toLowerCase();
}

function extendJsonViewStyles(baseStyles, additions) {
  return Object.fromEntries(
    Object.entries(baseStyles).map(([key, value]) => [key, joinJsonViewClasses(value, additions[key])]),
  );
}

function joinJsonViewClasses(baseClassName, extraClassName) {
  if (!extraClassName) {
    return baseClassName;
  }
  if (!baseClassName) {
    return extraClassName;
  }
  return `${baseClassName} ${extraClassName}`;
}

function MetadataInspector({ item }) {
  const [query, setQuery] = useState("");
  const normalizedQuery = normalizeSearchText(query);
  const filteredItem = useMemo(() => filterMetadataValue(item, normalizedQuery), [item, normalizedQuery]);
  const showEmptyState = !item;
  const showNoMatchState = Boolean(item) && normalizedQuery && filteredItem === undefined;

  useEffect(() => {
    setQuery("");
  }, [item]);

  return (
    <>
      <div className="inspector-header">
        <div>
          <h3>Metadata inspector</h3>
          <p>Inspect selected graph items as searchable JSON.</p>
        </div>
        <label className="inspector-search">
          <span>Search metadata</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search keys and values..."
            disabled={!item}
          />
        </label>
      </div>
      <div className="inspector-body">
        {showEmptyState ? (
          <div className="inspector-state">Select a node or edge.</div>
        ) : showNoMatchState ? (
          <div className="inspector-state">No matching metadata fields.</div>
        ) : (
          <JsonView
            aria-label="Metadata JSON viewer"
            clickToExpandNode
            compactTopLevel={false}
            data={filteredItem}
            shouldExpandNode={resolveInspectorExpansion}
            style={INSPECTOR_JSON_STYLES}
          />
        )}
      </div>
    </>
  );
}

function resolveInspectorExpansion(level, value, field) {
  if (field === "properties") {
    return true;
  }
  return collapseAllNested(level, value, field);
}

function filterMetadataValue(value, normalizedQuery) {
  if (!normalizedQuery) {
    return value;
  }
  return filterMetadataBranch(value, normalizedQuery);
}

function filterMetadataBranch(value, normalizedQuery) {
  if (value === null || value === undefined) {
    return matchesMetadataPrimitive(value, normalizedQuery) ? value : undefined;
  }

  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return matchesMetadataPrimitive(value, normalizedQuery) ? value : undefined;
  }

  if (Array.isArray(value)) {
    const filteredItems = value
      .map((item) => filterMetadataBranch(item, normalizedQuery))
      .filter((item) => item !== undefined);
    if (filteredItems.length > 0) {
      return filteredItems;
    }
    return value.some((item, index) => matchesMetadataComposite(index, item, normalizedQuery)) ? value : undefined;
  }

  if (typeof value === "object") {
    const filteredEntries = Object.entries(value).reduce((accumulator, [key, item]) => {
      const filteredItem = filterMetadataBranch(item, normalizedQuery);
      if (filteredItem !== undefined || normalizeSearchText(key).includes(normalizedQuery)) {
        accumulator[key] = filteredItem === undefined ? item : filteredItem;
      }
      return accumulator;
    }, {});
    if (Object.keys(filteredEntries).length > 0) {
      return filteredEntries;
    }
    return flattenSearchValue(value).toLowerCase().includes(normalizedQuery) ? value : undefined;
  }

  return normalizeSearchText(value).includes(normalizedQuery) ? value : undefined;
}

function matchesMetadataPrimitive(value, normalizedQuery) {
  return normalizeSearchText(value).includes(normalizedQuery);
}

function matchesMetadataComposite(key, value, normalizedQuery) {
  return normalizeSearchText(key).includes(normalizedQuery) || flattenSearchValue(value).includes(normalizedQuery);
}

function TurnContent({ turn }) {
  if (turn.role === "assistant" && turn.result_format === "text") {
    return (
      <div className="markdown-body">
        <ReactMarkdown
          rehypePlugins={[[rehypeHighlight, { ignoreMissing: true }]]}
          remarkPlugins={[remarkGfm]}
          components={MARKDOWN_COMPONENTS}
        >
          {turn.content}
        </ReactMarkdown>
      </div>
    );
  }
  return <pre>{turn.content}</pre>;
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

function resolveToolParameters(payload) {
  if (payload?.parameters && typeof payload.parameters === "object") {
    return payload.parameters;
  }
  if (payload?.metadata?.arguments && typeof payload.metadata.arguments === "object") {
    return payload.metadata.arguments;
  }
  if (payload?.arguments && typeof payload.arguments === "object") {
    return payload.arguments;
  }
  return null;
}

function resolveToolReason(payload) {
  if (typeof payload?.error_message === "string" && payload.error_message) {
    if (typeof payload?.error_type === "string" && payload.error_type) {
      return `${payload.error_type}: ${payload.error_message}`;
    }
    return payload.error_message;
  }
  return null;
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

function formatJson(value) {
  if (value === null || value === undefined) {
    return "none";
  }
  return JSON.stringify(value, null, 2);
}

function cryptoRandomId() {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const MARKDOWN_COMPONENTS = {
  a(props) {
    return <a {...props} rel="noreferrer" target="_blank" />;
  },
  pre(props) {
    return <pre className="markdown-code-block" {...props} />;
  },
  table(props) {
    return (
      <div className="markdown-table-wrap">
        <table {...props} />
      </div>
    );
  },
};
