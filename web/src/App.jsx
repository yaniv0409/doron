import { memo, useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { JsonView, collapseAllNested, darkStyles } from "react-json-view-lite";
import { ReactMarkdown } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import "highlight.js/styles/github-dark.css";
import "react-json-view-lite/dist/index.css";
import GraphPanel from "./GraphPanel";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const INITIAL_TURN_LIMIT = 12;

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
  const [graphTarget, setGraphTarget] = useState("memory");
  const [inspectedItem, setInspectedItem] = useState(null);
  const [activity, setActivity] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState("");
  const loadTokenRef = useRef(0);
  const currentSessionIdRef = useRef(null);

  useEffect(() => {
    void refreshSessions().catch(() => {});
  }, []);

  useEffect(() => {
    currentSessionIdRef.current = activeSession?.session_id ?? null;
  }, [activeSession?.session_id]);

  const turns = activeSession?.turns || [];
  const showThinkingBlock = isSending || activity.length > 0;
  const graphSummary = useMemo(() => {
    if (!activeSession) {
      return "0 nodes, 0 edges";
    }
    if (!graph.node_count || !graph.edge_count) {
      return `${graph.node_count || 0} nodes, ${graph.edge_count || 0} edges`;
    }
    if (graph.is_truncated) {
      return `${graph.node_count} nodes, ${graph.edge_count} edges, truncated`;
    }
    return `${graph.node_count} nodes, ${graph.edge_count} edges`;
  }, [activeSession, graph]);

  const refreshSessions = useCallback(async () => {
    const response = await fetch(`${API_BASE}/sessions`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(readError(payload));
    }
    setSessions(payload);
  }, []);

  const refreshGraph = useCallback(async (sessionId, token = loadTokenRef.current, target = graphTarget) => {
    const response = await fetch(`${API_BASE}/sessions/${sessionId}/graph/${target}`);
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    if (token !== loadTokenRef.current) {
      return;
    }
    if (currentSessionIdRef.current !== sessionId) {
      return;
    }
    setGraph(payload);
  }, [graphTarget]);

  useEffect(() => {
    if (!activeSession?.session_id) {
      return;
    }
    void refreshGraph(activeSession.session_id, loadTokenRef.current, graphTarget);
  }, [activeSession?.session_id, graphTarget, refreshGraph]);

  const loadSession = useCallback(
    async (sessionId, { turnLimit = INITIAL_TURN_LIMIT } = {}) => {
      const token = ++loadTokenRef.current;
      setError("");
      setInspectedItem(null);
      setActivity([]);
      setGraph(EMPTY_GRAPH);

      const response = await fetch(`${API_BASE}/sessions/${sessionId}?turn_limit=${turnLimit}`);
      const payload = await response.json();
      if (!response.ok) {
        if (token === loadTokenRef.current) {
          setError(readError(payload));
        }
        return false;
      }
      if (token !== loadTokenRef.current) {
        return false;
      }

      currentSessionIdRef.current = sessionId;
      setActiveSession(payload);
      void refreshGraph(sessionId, token, graphTarget);
      return true;
    },
    [graphTarget, refreshGraph],
  );

  const openSession = useCallback(
    async ({ name, dbMode, sessionGroupId, webToolCallLimit }) => {
      const response = await fetch(`${API_BASE}/sessions/open`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          use_dedicated_db: dbMode === "dedicated",
          session_group_id: dbMode === "group" ? sessionGroupId : null,
          web_tool_call_limit: toNullableNumber(webToolCallLimit),
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(readError(payload));
        return false;
      }
      await Promise.all([refreshSessions().catch(() => {}), loadSession(payload.session_id)]);
      return true;
    },
    [loadSession, refreshSessions],
  );

  const forkSession = useCallback(
    async ({
      sourceSessionId,
      name,
      groupName,
      inheritModelSettings,
      inheritOutputSchema,
      inheritRuntimeSettings,
      inheritContext,
    }) => {
      const response = await fetch(`${API_BASE}/sessions/${sourceSessionId}/fork`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          group_name: groupName || null,
          inherit_model_settings: inheritModelSettings,
          inherit_output_schema: inheritOutputSchema,
          inherit_runtime_settings: inheritRuntimeSettings,
          inherit_context: inheritContext,
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(readError(payload));
        return false;
      }
      await Promise.all([refreshSessions().catch(() => {}), loadSession(payload.session_id)]);
      return true;
    },
    [loadSession, refreshSessions],
  );

  const saveSessionSettings = useCallback(
    async (sessionId, webToolCallLimit) => {
      const response = await fetch(`${API_BASE}/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          web_tool_call_limit: toNullableNumber(webToolCallLimit),
        }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(readError(payload));
        return false;
      }
      setActiveSession(payload);
      await refreshSessions().catch(() => {});
      return true;
    },
    [refreshSessions],
  );

  const stopSession = useCallback(
    async (sessionId, mode) => {
      const response = await fetch(`${API_BASE}/sessions/${sessionId}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode }),
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(readError(payload));
        return false;
      }
      setActiveSession(payload);
      await Promise.all([refreshSessions().catch(() => {}), refreshGraph(sessionId).catch(() => {})]);
      return true;
    },
    [refreshGraph, refreshSessions],
  );

  const resumeSession = useCallback(
    async (sessionId) => {
      const response = await fetch(`${API_BASE}/sessions/${sessionId}/resume`, {
        method: "POST",
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(readError(payload));
        return false;
      }
      setActiveSession(payload);
      await Promise.all([refreshSessions().catch(() => {}), refreshGraph(sessionId).catch(() => {})]);
      return true;
    },
    [refreshGraph, refreshSessions],
  );

  const loadOlderTurns = useCallback(
    async (sessionId, beforeMessageId) => {
      if (!beforeMessageId) {
        return false;
      }
      const response = await fetch(
        `${API_BASE}/sessions/${sessionId}/turns?limit=${INITIAL_TURN_LIMIT}&before=${encodeURIComponent(beforeMessageId)}`,
      );
      const payload = await response.json();
      if (!response.ok) {
        setError(readError(payload));
        return false;
      }
      setActiveSession((current) => {
        if (!current || current.session_id !== sessionId) {
          return current;
        }
        return {
          ...current,
          turns: [...payload.turns, ...current.turns],
          turn_count: payload.turn_count,
          has_more_turns: payload.has_more_turns,
          oldest_turn_message_id: payload.oldest_turn_message_id,
          newest_turn_message_id: current.newest_turn_message_id || payload.newest_turn_message_id,
        };
      });
      return true;
    },
    [],
  );

  const sendMessage = useCallback(
    async ({ sessionId, message, webToolCallLimit }) => {
      const trimmedMessage = message.trim();
      if (!trimmedMessage) {
        return false;
      }
      if (isSending) {
        const optimisticTurn = createTurn({
          role: "steer",
          content: trimmedMessage,
        });
        setError("");
        setActiveSession((current) => appendTurn(current, sessionId, optimisticTurn));
        const response = await fetch(`${API_BASE}/sessions/${sessionId}/steer`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: trimmedMessage }),
        });
        const payload = await response.json();
        if (!response.ok) {
          setError(readError(payload));
          return false;
        }
        setActiveSession(payload);
        await refreshSessions().catch(() => {});
        return true;
      }

      const optimisticTurn = createTurn({
        role: "user",
        content: trimmedMessage,
        webToolCallLimitUsed: toNullableNumber(webToolCallLimit),
      });

      setIsSending(true);
      setError("");
      setActivity([]);
      setActiveSession((current) => appendTurn(current, sessionId, optimisticTurn));

      const response = await fetch(`${API_BASE}/sessions/${sessionId}/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: trimmedMessage,
          web_tool_call_limit: toNullableNumber(webToolCallLimit),
        }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json();
        setError(readError(payload));
        setIsSending(false);
        return false;
      }

      await consumeSse(response.body, (eventName, payload) => {
        handleStreamEvent(eventName, payload, {
          sessionId,
          submittedMessage: trimmedMessage,
        });
      });

      setIsSending(false);
      void refreshSessions().catch(() => {});
      return true;
    },
    [isSending, refreshSessions],
  );

  const handleStreamEvent = useCallback(
    (eventName, payload, context) => {
      setActivity((items) => [...items, buildActivityEntry(eventName, payload)]);
      if (eventName === "session.message.failed") {
        setError(readError(payload));
      }
      if (eventName === "session.message.completed" || eventName === "session.message.failed") {
        setActiveSession((current) => {
          if (!current || current.session_id !== context.sessionId) {
            return current;
          }
          const assistantTurn = createTurn({
            role: "assistant",
            content:
              payload.assistant_message ||
              payload.error?.message ||
              payload.message ||
              "Request failed.",
            traceId: payload.trace_id || null,
            status: payload.status || (eventName === "session.message.failed" ? "failed" : "completed"),
            resultFormat: payload.result_format || "text",
            webToolCallLimitUsed: payload.web_tool_call_limit_used ?? current.web_tool_call_limit,
            completion: payload.completion || null,
          });
          return appendTurn(current, current.session_id, assistantTurn);
        });
      }
      if (eventName === "session.steered" || eventName === "session.restarted") {
        setError("");
      }
      if (eventName === "session.graph.updated" && payload.session_id) {
        void refreshGraph(payload.session_id);
      }
    },
    [refreshGraph],
  );

  const handleSelectSession = useCallback(
    (sessionId) => {
      void loadSession(sessionId);
    },
    [loadSession],
  );

  return (
    <div className="app-shell">
      <SessionSidebar
        activeSessionId={activeSession?.session_id ?? null}
        onForkSession={forkSession}
        onOpenSession={openSession}
        onSelectSession={handleSelectSession}
        sessions={sessions}
      />

      <main className="workspace">
        <ChatColumn
          activeSession={activeSession}
          activity={activity}
          error={error}
          isSending={isSending}
          onLoadOlderTurns={loadOlderTurns}
          onSaveSessionSettings={saveSessionSettings}
          onResumeSession={resumeSession}
          onStopSession={stopSession}
          onSendMessage={sendMessage}
          showThinkingBlock={showThinkingBlock}
          turns={turns}
        />

        <GraphColumn
          graph={graph}
          graphTarget={graphTarget}
          graphSummary={graphSummary}
          inspectedItem={inspectedItem}
          onGraphTargetChange={setGraphTarget}
          onInspect={setInspectedItem}
        />
      </main>
    </div>
  );
}

function SessionSidebar({ activeSessionId, onForkSession, onOpenSession, onSelectSession, sessions }) {
  const [sessionName, setSessionName] = useState("");
  const [dbMode, setDbMode] = useState("shared");
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [webToolCallLimit, setWebToolCallLimit] = useState("");
  const [contextMenu, setContextMenu] = useState(null);
  const [forkDraft, setForkDraft] = useState(null);

  const sessionGroups = useMemo(() => buildSessionGroups(sessions), [sessions]);
  const groupedSessions = useMemo(() => buildSidebarEntries(sessions), [sessions]);

  useEffect(() => {
    function handleWindowClick() {
      setContextMenu(null);
    }
    window.addEventListener("click", handleWindowClick);
    return () => window.removeEventListener("click", handleWindowClick);
  }, []);

  useEffect(() => {
    if (dbMode !== "group") {
      return;
    }
    if (selectedGroupId) {
      return;
    }
    if (sessionGroups.length > 0) {
      setSelectedGroupId(sessionGroups[0].session_group_id);
    }
  }, [dbMode, selectedGroupId, sessionGroups]);

  async function handleSubmit(event) {
    event.preventDefault();
    const ok = await onOpenSession({
      name: sessionName,
      dbMode,
      sessionGroupId: selectedGroupId,
      webToolCallLimit,
    });
    if (ok) {
      setSessionName("");
      setDbMode("shared");
      setSelectedGroupId("");
      setWebToolCallLimit("");
    }
  }

  function handleContextMenu(event, session) {
    event.preventDefault();
    setContextMenu({
      session,
      x: event.clientX,
      y: event.clientY,
    });
  }

  function openForkDialog(session) {
    setContextMenu(null);
    setForkDraft({
      sourceSessionId: session.session_id,
      sourceSession: session,
      name: `${session.name} Fork`,
      groupName: session.session_group_name || session.name,
      inheritModelSettings: true,
      inheritOutputSchema: true,
      inheritRuntimeSettings: true,
      inheritContext: false,
    });
  }

  async function submitFork(event) {
    event.preventDefault();
    if (!forkDraft) {
      return;
    }
    const ok = await onForkSession(forkDraft);
    if (ok) {
      setForkDraft(null);
    }
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <div className="brand">
          <h1>Doron</h1>
          <p>Research sessions with live graph memory.</p>
        </div>
      </div>

      <div className="sidebar-body">
        <form className="card form-stack" onSubmit={handleSubmit}>
          <label>
            Session name
            <input value={sessionName} onChange={(event) => setSessionName(event.target.value)} required />
          </label>
          <label>
            DB target
            <select value={dbMode} onChange={(event) => setDbMode(event.target.value)}>
              <option value="shared">Standalone shared DB</option>
              <option value="dedicated">Dedicated DB</option>
              <option value="group" disabled={sessionGroups.length === 0}>
                Existing group
              </option>
            </select>
          </label>
          {dbMode === "group" ? (
            <label>
              Existing group
              <select value={selectedGroupId} onChange={(event) => setSelectedGroupId(event.target.value)} required>
                {sessionGroups.map((group) => (
                  <option key={group.session_group_id} value={group.session_group_id}>
                    {group.session_group_name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            Session web limit
            <input
              type="number"
              min="0"
              value={webToolCallLimit}
              onChange={(event) => setWebToolCallLimit(event.target.value)}
              placeholder="shared default"
            />
          </label>
          <button type="submit">Open or resume</button>
        </form>

        <div className="card session-list">
          <h2>Sessions</h2>
          {groupedSessions.map((entry) =>
            entry.kind === "group" ? (
              <div className="session-group" key={entry.group.session_group_id}>
                <div className="session-group-header">
                  <strong>{entry.group.session_group_name}</strong>
                  <small>{entry.group.db_dir}</small>
                </div>
                <div className="session-group-items">
                  {entry.sessions.map((session) => (
                    <button
                      className={`session-item grouped ${activeSessionId === session.session_id ? "active" : ""}`}
                      key={session.session_id}
                      onClick={() => onSelectSession(session.session_id)}
                      onContextMenu={(event) => handleContextMenu(event, session)}
                      type="button"
                    >
                      <span>{session.name}</span>
                      <small>{formatSessionStatus(session)}</small>
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <button
                className={`session-item ${activeSessionId === entry.session.session_id ? "active" : ""}`}
                key={entry.session.session_id}
                onClick={() => onSelectSession(entry.session.session_id)}
                onContextMenu={(event) => handleContextMenu(event, entry.session)}
                type="button"
              >
                <span>{entry.session.name}</span>
                <small>{formatSessionStatus(entry.session)}</small>
              </button>
            ),
          )}
        </div>
      </div>
      {contextMenu ? (
        <div className="context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} onClick={(event) => event.stopPropagation()}>
          <button type="button" onClick={() => openForkDialog(contextMenu.session)}>
            Fork session
          </button>
        </div>
      ) : null}
      {forkDraft ? (
        <div className="modal-backdrop" onClick={() => setForkDraft(null)}>
          <div className="modal-card card" onClick={(event) => event.stopPropagation()}>
            <form className="form-stack" onSubmit={submitFork}>
              <div>
                <h2>Fork session</h2>
                <p>{forkDraft.sourceSession.name}</p>
              </div>
              <label>
                Session name
                <input
                  required
                  value={forkDraft.name}
                  onChange={(event) => setForkDraft((current) => ({ ...current, name: event.target.value }))}
                />
              </label>
              {forkDraft.sourceSession.session_group_id ? (
                <label>
                  Group name
                  <input disabled value={forkDraft.sourceSession.session_group_name || ""} readOnly />
                </label>
              ) : (
                <label>
                  Group name
                  <input
                    required
                    value={forkDraft.groupName}
                    onChange={(event) => setForkDraft((current) => ({ ...current, groupName: event.target.value }))}
                  />
                </label>
              )}
              <label className="checkbox">
                <input
                  checked={forkDraft.inheritModelSettings}
                  type="checkbox"
                  onChange={(event) =>
                    setForkDraft((current) => ({ ...current, inheritModelSettings: event.target.checked }))
                  }
                />
                Inherit model settings
              </label>
              <label className="checkbox">
                <input
                  checked={forkDraft.inheritOutputSchema}
                  type="checkbox"
                  onChange={(event) =>
                    setForkDraft((current) => ({ ...current, inheritOutputSchema: event.target.checked }))
                  }
                />
                Inherit output schema
              </label>
              <label className="checkbox">
                <input
                  checked={forkDraft.inheritRuntimeSettings}
                  type="checkbox"
                  onChange={(event) =>
                    setForkDraft((current) => ({ ...current, inheritRuntimeSettings: event.target.checked }))
                  }
                />
                Inherit runtime settings
              </label>
              <label className="checkbox">
                <input
                  checked={forkDraft.inheritContext}
                  type="checkbox"
                  onChange={(event) => setForkDraft((current) => ({ ...current, inheritContext: event.target.checked }))}
                />
                Inherit context
              </label>
              <div className="modal-actions">
                <button type="button" onClick={() => setForkDraft(null)}>
                  Cancel
                </button>
                <button type="submit">Fork</button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </aside>
  );
}

function ChatColumn({
  activeSession,
  activity,
  error,
  isSending,
  onLoadOlderTurns,
  onSaveSessionSettings,
  onResumeSession,
  onStopSession,
  onSendMessage,
  showThinkingBlock,
  turns,
}) {
  return (
    <section className="chat-column">
      <div className="chat-header card">
        <div>
          <h2>{activeSession?.name || "No session selected"}</h2>
          <p>{activeSession?.db_dir || "Open a session to begin."}</p>
        </div>
        {activeSession ? (
          <SessionControls
            activeSession={activeSession}
            onResumeSession={onResumeSession}
            onSaveSessionSettings={onSaveSessionSettings}
            onStopSession={onStopSession}
          />
        ) : null}
      </div>

      {activeSession?.has_more_turns ? (
        <div className="card load-more-row">
          <button
            disabled={isSending}
            onClick={() => onLoadOlderTurns(activeSession.session_id, activeSession.oldest_turn_message_id)}
            type="button"
          >
            Load older turns
          </button>
        </div>
      ) : null}

      <div className="chat-log">
        {turns.map((turn) => (
          <article className={`turn ${turn.role}`} key={turn.message_id}>
            <header>
              <strong>{formatTurnAuthor(turn.role)}</strong>
              <span>{turn.web_tool_call_limit_used ?? "-"}</span>
            </header>
            <TurnContent turn={turn} />
          </article>
        ))}
        {showThinkingBlock ? (
          <article className="turn assistant thinking-turn" key="live-activity">
            <header>
              <strong>Doron</strong>
              <span>{isSending ? "thinking" : "activity"}</span>
            </header>
            <div className="thinking-shell">
              <div className="thinking-label">Live activity</div>
              <div className="activity-log">
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

      <Composer
        activeSession={activeSession}
        isSending={isSending}
        onSendMessage={onSendMessage}
      />
      {error ? <div className="error-banner">{error}</div> : null}
    </section>
  );
}

function SessionLimitEditor({ activeSession, onSaveSessionSettings }) {
  const [sessionWebLimit, setSessionWebLimit] = useState("");

  useEffect(() => {
    setSessionWebLimit(activeSession?.web_tool_call_limit ?? "");
  }, [activeSession?.session_id, activeSession?.web_tool_call_limit]);

  async function handleSave() {
    await onSaveSessionSettings(activeSession.session_id, sessionWebLimit);
  }

  return (
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
      <button onClick={handleSave} type="button">
        Save
      </button>
    </div>
  );
}

function SessionControls({ activeSession, onResumeSession, onSaveSessionSettings, onStopSession }) {
  const isStopped = Boolean(activeSession.stop_mode) || activeSession.is_closed;
  return (
    <div className="session-controls">
      <SessionLimitEditor activeSession={activeSession} onSaveSessionSettings={onSaveSessionSettings} />
      <div className="stop-actions">
        {isStopped ? (
          <button type="button" onClick={() => onResumeSession(activeSession.session_id)}>
            Resume
          </button>
        ) : null}
        {!activeSession.is_closed ? (
          <>
            <button type="button" onClick={() => onStopSession(activeSession.session_id, "soft")}>
              Soft stop
            </button>
            <button type="button" className="danger" onClick={() => onStopSession(activeSession.session_id, "hard")}>
              Hard stop
            </button>
          </>
        ) : null}
      </div>
    </div>
  );
}

function Composer({ activeSession, isSending, onSendMessage }) {
  const [message, setMessage] = useState("");
  const [messageWebLimit, setMessageWebLimit] = useState("");

  async function handleSubmit(event) {
    event.preventDefault();
    if (!activeSession) {
      return;
    }
    const ok = await onSendMessage({
      sessionId: activeSession.session_id,
      message,
      webToolCallLimit: messageWebLimit,
    });
    if (ok) {
      setMessage("");
      setMessageWebLimit("");
    }
  }

  return (
    <form className="composer card" onSubmit={handleSubmit}>
      <textarea
        disabled={!activeSession || activeSession.is_closed}
        value={message}
        onChange={(event) => setMessage(event.target.value)}
        placeholder={isSending ? "Steer the current run..." : "Ask Doron to research, compare, inspect, or write into the graph..."}
        rows={5}
      />
      <div className="composer-controls">
        <label>
          Message web limit
          <input
            disabled={!activeSession || isSending || activeSession.is_closed}
            type="number"
            min="0"
            value={messageWebLimit}
            onChange={(event) => setMessageWebLimit(event.target.value)}
            placeholder="use session default"
          />
        </label>
        <button disabled={!activeSession || activeSession.is_closed} type="submit">
          {activeSession?.is_closed ? "Stopped" : isSending ? "Steer" : "Send"}
        </button>
      </div>
    </form>
  );
}

function GraphColumn({ graph, graphSummary, graphTarget, inspectedItem, onGraphTargetChange, onInspect }) {
  const [graphSearchQuery, setGraphSearchQuery] = useState("");
  const deferredGraphSearchQuery = useDeferredValue(graphSearchQuery);
  const filteredGraph = useMemo(
    () => filterGraph(graph, deferredGraphSearchQuery),
    [deferredGraphSearchQuery, graph],
  );

  return (
    <section className="graph-column">
      <div className="card graph-header">
        <div className="graph-header-copy">
          <h2>Living graph</h2>
          <p>{graphSummary}</p>
          <div className="graph-legend">
            <span className="graph-legend-swatch" aria-hidden="true" />
            <span>Hue = node or edge type, node brightness = visible connectivity</span>
          </div>
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
        <label className="graph-search">
          <span>Graph target</span>
          <select value={graphTarget} onChange={(event) => onGraphTargetChange(event.target.value)}>
            <option value="memory">Free-form memory</option>
            <option value="research_meta">Research metadata</option>
          </select>
        </label>
      </div>
      <div className="card graph-wrap">
        <GraphPanel graph={filteredGraph} onInspect={onInspect} />
      </div>
      <div className="card inspector">
        <MetadataInspector item={inspectedItem} />
      </div>
    </section>
  );
}

function MetadataInspector({ item }) {
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const normalizedQuery = normalizeSearchText(deferredQuery);
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

const TurnContent = memo(function TurnContent({ turn }) {
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
});

function appendTurn(session, sessionId, turn) {
  if (!session || session.session_id !== sessionId) {
    return session;
  }
  const turns = [...session.turns, turn];
  return {
    ...session,
    turns,
    turn_count: Math.max(session.turn_count || session.turns.length, turns.length),
    newest_turn_message_id: turn.message_id,
  };
}

function createTurn({
  content,
  role,
  traceId = null,
  status = "completed",
  resultFormat = "text",
  webToolCallLimitUsed = null,
  completion = null,
}) {
  return {
    message_id: cryptoRandomId(),
    role,
    content,
    created_at: new Date().toISOString(),
    trace_id: traceId,
    status,
    result_format: resultFormat,
    web_tool_call_limit_used: webToolCallLimitUsed,
    completion,
  };
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
  if (eventName === "session.steered") {
    return { id, kind: "text", text: `Steer queued: ${payload.message || ""}`.trim() };
  }
  if (eventName === "session.restarted") {
    return { id, kind: "text", text: "Mission restarted with updated steering." };
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

function formatSessionStatus(session) {
  const statusBits = [
    session.session_group_id ? `Group: ${session.session_group_name}` : session.uses_dedicated_db ? "Dedicated DB" : "Shared DB",
  ];
  if (session.is_closed) {
    statusBits.push("Hard stopped");
  } else if (session.stop_mode === "soft") {
    statusBits.push("Soft stop");
  }
  return statusBits.join(" • ");
}

function formatTurnAuthor(role) {
  if (role === "user") {
    return "You";
  }
  if (role === "steer") {
    return "Steer";
  }
  return "Doron";
}

function buildSessionGroups(sessions) {
  const groups = new Map();
  for (const session of sessions) {
    if (!session.session_group_id) {
      continue;
    }
    if (!groups.has(session.session_group_id)) {
      groups.set(session.session_group_id, {
        session_group_id: session.session_group_id,
        session_group_name: session.session_group_name || "Session group",
        db_dir: session.db_dir,
      });
    }
  }
  return Array.from(groups.values()).sort((left, right) => left.session_group_name.localeCompare(right.session_group_name));
}

function buildSidebarEntries(sessions) {
  const grouped = new Map();
  const standalone = [];
  for (const session of sessions) {
    if (!session.session_group_id) {
      standalone.push({ kind: "session", session });
      continue;
    }
    if (!grouped.has(session.session_group_id)) {
      grouped.set(session.session_group_id, {
        kind: "group",
        group: {
          session_group_id: session.session_group_id,
          session_group_name: session.session_group_name || "Session group",
          db_dir: session.db_dir,
        },
        sessions: [],
      });
    }
    grouped.get(session.session_group_id).sessions.push(session);
  }
  const groupEntries = Array.from(grouped.values()).sort((left, right) =>
    left.group.session_group_name.localeCompare(right.group.session_group_name),
  );
  return [...standalone, ...groupEntries];
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

function resolveInspectorExpansion(level, value, field) {
  if (field === "properties") {
    return true;
  }
  return collapseAllNested(level, value, field);
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
