import { useEffect, useRef, useState } from "react";

// 预设案例只提供快捷输入文字；实际追问、风险判断和结果都由后端决定。
const CASES = [
  {
    id: "01",
    title: "动态追问",
    detail: "从简短描述开始，逐步补充信息",
    message: "头部不适",
  },
  {
    id: "02",
    title: "信息足够",
    detail: "直接查看结构化整理结果",
    message: "头部不适两天，前额，程度轻，暂无其他不适，无病史",
  },
  {
    id: "03",
    title: "预设风险",
    detail: "体验规则优先的安全分流",
    message: "模拟人物突然呼吸困难",
  },
];

// 浏览器只缓存会话 ID 和保存时间。完整对话与问诊状态保存在后端数据库中。
const SESSION_KEY = "consultation_demo_session";
const SESSION_TTL = 30 * 60 * 1000;

// 页面刷新时尝试找回最近 30 分钟的会话，同时兼容旧版的缓存键名。
function loadSession() {
  try {
    const item = JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
    if (item?.id && Date.now() - item.savedAt < SESSION_TTL) return item.id;
  } catch {
    /* 无效缓存 */
  }
  localStorage.removeItem(SESSION_KEY);
  const legacyId = localStorage.getItem("session_id");
  const legacyTime = Number(localStorage.getItem("session_ts"));
  localStorage.removeItem("session_id");
  localStorage.removeItem("session_ts");
  if (legacyId && legacyTime && Date.now() - legacyTime < SESSION_TTL) {
    saveSession(legacyId);
    return legacyId;
  }
  return null;
}

// 每次成功请求后刷新保存时间，让仍在使用的会话继续保持可恢复。
function saveSession(id) {
  localStorage.setItem(SESSION_KEY, JSON.stringify({ id, savedAt: Date.now() }));
}
function clearSession() {
  localStorage.removeItem(SESSION_KEY);
}

// 统一处理 JSON 响应，并保留 HTTP 状态码供发送逻辑判断失效会话。
async function requestJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || "请求失败，请稍后重试");
    error.status = response.status;
    throw error;
  }
  return data;
}

// 首条消息通过创建会话接口发送，后续消息走 /messages 接口。
function createSession(message) {
  return requestJson("/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ initial_message: message }),
  });
}

// 重开时尽量清理旧会话；网络故障不阻止用户尝试开启新会话。
async function discardSession(id) {
  if (!id) return;
  try {
    await fetch("/sessions/" + encodeURIComponent(id), { method: "DELETE" });
  } catch {
    /* 断网时仍可尝试新建会话 */
  }
}

export default function App() {
  // React state 会触发界面更新：会话 ID、消息、后端状态和输入框内容分别保存。
  const [sessionId, setSessionId] = useState(loadSession);
  const [messages, setMessages] = useState([]);
  const [state, setState] = useState(null);
  const [input, setInput] = useState("");
  const [activeCase, setActiveCase] = useState(null);
  const [loading, setLoading] = useState(false);
  const [restoring, setRestoring] = useState(Boolean(sessionId));
  const [error, setError] = useState("");

  // ref 修改后不会触发渲染：busy 立即拦住重复点击，chatEnd 用于滚动到底部。
  const busy = useRef(false);
  const chatEnd = useRef(null);

  // 首次进入页面时，用缓存的 ID 从后端恢复完整会话。
  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    requestJson("/sessions/" + encodeURIComponent(sessionId))
      .then((data) => {
        if (cancelled) return;
        setState(data);
        // 新接口返回完整 conversation；旧接口才需要用最近一条回复兜底。
        const legacyReply = data.rendered_text || data.asked_questions?.at(-1);
        setMessages(
          data.conversation?.length
            ? data.conversation
            : legacyReply
              ? [{ role: "agent", text: legacyReply }]
              : [],
        );
        saveSession(sessionId);
      })
      .catch(() => {
        if (cancelled) return;
        clearSession();
        setSessionId(null);
      })
      .finally(() => {
        if (!cancelled) setRestoring(false);
      });
    return () => {
      // 组件卸载后忽略尚未返回的请求，避免旧响应覆盖当前页面。
      cancelled = true;
    };
    // 仅在页面加载时恢复，之后使用每次请求的完整响应。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 新消息或“正在整理信息”出现时，让对话视图跟随到底部。
  useEffect(() => {
    chatEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, loading]);

  // 下列值都从后端状态推导，供页面展示，不参与后端问诊决策。
  const result = state?.final_result || {};
  const hasResult = Boolean(result.status);
  const isRisk = result.status === "risk_route";
  const facts = Object.entries(state?.collected_facts || {});
  const missing = state?.missing_fields || [];
  // 进度条最多展示 3 次追问；真实次数仍以 state.followup_count 为准。
  const followupCount = Math.min(state?.followup_count || 0, 3);
  const phase = isRisk
    ? "风险分流"
    : hasResult
      ? "本轮已完成"
      : messages.length
        ? "等待补充"
        : "准备开始";

  // raw 是待发送文字；fresh 表示强制新建会话；caseName 用于高亮选中的案例。
  async function handleSend(raw = input, fresh = false, caseName = null) {
    const message = raw.trim();
    // restoring 时还没确定旧会话状态；busy 在 React 重新渲染前就能阻止重复发送。
    if (!message || busy.current || restoring) return;
    busy.current = true;
    setLoading(true);
    setError("");
    setInput("");
    // 旧会话已有最终结果时，新输入自动成为下一轮独立会话的首条消息。
    const startNew = fresh || hasResult || !sessionId;
    const previousMessages = messages;
    const previousState = state;
    // 先在界面显示用户消息，减少等待感；失败时会恢复原来的内容。
    if (startNew) {
      setMessages([{ role: "user", text: message }]);
      setState(null);
      setActiveCase(caseName);
    } else {
      setMessages((items) => [...items, { role: "user", text: message }]);
    }

    try {
      let data;
      if (startNew) {
        // 选择预设案例或重新开始后，不能把旧会话的风险状态带到新会话。
        await discardSession(sessionId);
        data = await createSession(message);
      } else {
        try {
          data = await requestJson(
            "/sessions/" + encodeURIComponent(sessionId) + "/messages",
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ message }),
            },
          );
        } catch (requestError) {
          // 本地缓存对应的会话已不存在或已结束时，以当前输入创建新会话。
          if (requestError.status !== 404 && requestError.status !== 409)
            throw requestError;
          clearSession();
          data = await createSession(message);
          setActiveCase(null);
        }
      }
      setSessionId(data.session_id);
      saveSession(data.session_id);
      setState(data);
      // 用后端返回的完整记录替换刚才临时显示的消息，保持前后端一致。
      setMessages(data.conversation || []);
    } catch (requestError) {
      // 请求失败：撤销临时消息，并把文字放回输入框，方便用户重试。
      setMessages(previousMessages);
      setState(previousState);
      setInput(message);
      setError(requestError.message || "连接失败，请稍后重试。");
    } finally {
      busy.current = false;
      setLoading(false);
    }
  }

  // 清空当前前端状态与本地缓存，并请求后端删除对应会话。
  function handleReset() {
    if (busy.current || restoring) return;
    discardSession(sessionId);
    clearSession();
    setSessionId(null);
    setState(null);
    setMessages([]);
    setInput("");
    setActiveCase(null);
    setError("");
  }

  return (
    <div className="app-shell">
      {/* 顶部品牌与页面介绍是静态内容，不随会话变化。 */}
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">✳</span>
          <span>
            Consult<span className="brand-light">/Lab</span>
          </span>
        </div>
        <span className="topbar-meta">DAY 15 · INTERACTIVE DEMO</span>
      </header>
      <main className="page-content">
        <section className="intro" aria-labelledby="page-title">
          <div className="eyebrow">
            <span className="eyebrow-dot" /> 模拟问诊工作台
          </div>
          <h1 id="page-title">
            让每一步问诊，
            <br />
            <span>都有迹可循。</span>
          </h1>
          <p>
            选择一个演示场景，或输入虚构病例。
            系统会展示追问、信息整理与预设风险分流的过程。
          </p>
          <div className="notice">
            <span aria-hidden="true">ⓘ</span>
            <span>仅限虚构病例演示，不提供真实医疗诊断、治疗或用药指导。</span>
          </div>
        </section>

        <section className="scenarios" aria-labelledby="scenario-title">
          <div className="section-heading">
            <div>
              <span className="section-kicker">QUICK START</span>
              <h2 id="scenario-title">选择演示场景</h2>
            </div>
            <span className="section-caption">每次选择都会开启独立会话</span>
          </div>
          <div className="scenario-grid">
            {/* fresh=true：每次点击预设案例都开启独立会话。 */}
            {CASES.map((item) => (
              <button
                key={item.id}
                className={
                  "scenario-card" + (activeCase === item.title ? " selected" : "")
                }
                onClick={() => handleSend(item.message, true, item.title)}
                disabled={loading || restoring}
              >
                <span className="scenario-number">{item.id}</span>
                <span className="scenario-title">
                  {item.title}
                  <span className="scenario-arrow" aria-hidden="true">
                    ↗
                  </span>
                </span>
                <span className="scenario-description">{item.detail}</span>
              </button>
            ))}
          </div>
        </section>

        <section className="workspace" aria-label="问诊演示工作台">
          <div className="chat-panel">
            <div className="panel-header">
              <div>
                <span className="section-kicker">CONVERSATION</span>
                <h2>对话记录</h2>
              </div>
              <div className="panel-actions">
                <span className={"phase-pill" + (isRisk ? " phase-risk" : "")}>
                  <span className="phase-dot" />
                  {phase}
                </span>
                <button
                  className="text-button"
                  onClick={handleReset}
                  disabled={loading || restoring}
                >
                  重新开始
                </button>
              </div>
            </div>
            {/* role="log" 和 aria-live 让辅助技术读到新增的对话内容。 */}
            <div
              className="chat-stream"
              role="log"
              aria-live="polite"
              aria-relevant="additions text"
            >
              {/* 恢复中、空会话和已有消息分别显示不同内容。 */}
              {restoring ? (
                <div className="empty-chat">正在恢复会话…</div>
              ) : messages.length === 0 ? (
                <div className="empty-chat">
                  <div className="empty-icon">✳</div>
                  <h3>从一个模拟案例开始</h3>
                  <p>上方选择场景，或在下方输入虚构人物的症状描述。</p>
                </div>
              ) : (
                messages.map((item, index) => (
                  <div
                    className={
                      "message-row " +
                      (item.role === "user" ? "from-user" : "from-agent")
                    }
                    key={index}
                  >
                    <div
                      className={
                        "avatar " +
                        (item.role === "user" ? "user-avatar" : "agent-avatar")
                      }
                    >
                      {item.role === "user" ? "我" : "✳"}
                    </div>
                    <div className="message-content">
                      <div className="message-label">
                        {item.role === "user" ? "模拟用户" : "问诊 Agent"}
                      </div>
                      {/* 风险样式只标记本轮最后一条 Agent 回复。 */}
                      <div
                        className={
                          "message-bubble" +
                          (isRisk &&
                          index === messages.length - 1 &&
                          item.role === "agent"
                            ? " risk-bubble"
                            : "")
                        }
                      >
                        {item.text}
                      </div>
                    </div>
                  </div>
                ))
              )}
              {/* 等待接口响应时显示临时提示，收到结果后由真实对话替换。 */}
              {loading && (
                <div className="message-row from-agent">
                  <div className="avatar agent-avatar">✳</div>
                  <div className="message-content">
                    <div className="message-label">问诊 Agent</div>
                    <div className="message-bubble typing">正在整理信息…</div>
                  </div>
                </div>
              )}
              <div ref={chatEnd} />
            </div>
            <div className="composer-wrap">
              {/* 错误在输入框上方提示；会话结束后说明下一次发送会自动重开。 */}
              {error && (
                <div className="request-error" role="alert">
                  {error}
                </div>
              )}
              {hasResult && (
                <div className="new-session-note">
                  本轮已结束。继续输入将自动开启新的模拟会话。
                </div>
              )}
              <div className="composer">
                <textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    // Enter 发送；Shift+Enter 换行；中文输入法组字时不误发送。
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      handleSend();
                    }
                  }}
                  placeholder="描述一个虚构病例，或补充上一轮回答…"
                  aria-label="输入模拟病例描述"
                  rows={2}
                  disabled={loading || restoring}
                />
                <button
                  className="send-button"
                  onClick={() => handleSend()}
                  disabled={!input.trim() || loading || restoring}
                  aria-label="发送消息"
                >
                  发送 <span aria-hidden="true">↗</span>
                </button>
              </div>
              <div className="composer-hint">Enter 发送 · Shift + Enter 换行</div>
            </div>
          </div>

          {/* 右侧面板只读取后端返回的状态，展示追问进度和整理结果。 */}
          <aside className="insight-panel" aria-label="会话状态与结果">
            <div className="insight-header">
              <span className="section-kicker">LIVE STATE</span>
              <h2>当前会话</h2>
              <p>信息会随每轮回答更新，并保存在本地会话库中。</p>
            </div>
            <div className="progress-block">
              <div className="progress-title">
                <span>追问进度</span>
                <strong>
                  {followupCount}
                  <span> / 3</span>
                </strong>
              </div>
              <div className="progress-track">
                <span style={{ width: (followupCount / 3) * 100 + "%" }} />
              </div>
              <p>每次只提出一个问题，最多追问三次。</p>
            </div>
            <div className="insight-section">
              <h3>
                已收集信息 <span>{facts.length}</span>
              </h3>
              {facts.length ? (
                <div className="fact-list">
                  {facts.map(([field, values]) => (
                    <div className="fact-item" key={field}>
                      <span>{field}</span>
                      <strong>{values.join("、")}</strong>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="muted">开始演示后，这里会显示已识别的信息。</p>
              )}
            </div>
            <div className="insight-section">
              <h3>
                尚待确认 <span>{missing.length}</span>
              </h3>
              {missing.length ? (
                <div className="chip-list">
                  {missing.map((field) => (
                    <span className="chip" key={field}>
                      {field}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="muted">暂无待确认字段。</p>
              )}
            </div>
            {/* 有风险标记时单独提示；最终结果只展示后端明确提供的字段。 */}
            {state?.risk_flags?.length > 0 && (
              <div className="risk-alert">
                <span className="risk-alert-label">预设规则已触发</span>
                <strong>{state.risk_flags.join("、")}</strong>
                <p>本轮已转入固定风险提示。</p>
              </div>
            )}
            {hasResult && (
              <div className={"result-summary" + (isRisk ? " result-risk" : "")}>
                <span className="section-kicker">SESSION RESULT</span>
                <h3>
                  {isRisk
                    ? "风险提示"
                    : result.status === "completed"
                      ? "整理结果"
                      : "处理状态"}
                </h3>
                {result.next_step && <p className="result-next">{result.next_step}</p>}
                {result.risk_message && <p>{result.risk_message}</p>}
                {result.tools_used?.length > 0 && (
                  <div className="result-tools">
                    已调用工具：{result.tools_used.join("、")}
                  </div>
                )}
              </div>
            )}
          </aside>
        </section>
        <footer>
          CONSULT / LAB <span>·</span> 虚构病例交互演示
        </footer>
      </main>
    </div>
  );
}
